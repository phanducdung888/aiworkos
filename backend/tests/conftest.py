"""Test fixtures.

Tests run against a real PostgreSQL. SQLite would test a different system: RLS, triggers, partial
indexes, composite foreign keys and `jsonb` are all load-bearing here, and none of them exist there.

Two connections are exposed on purpose:

* `owner_engine` — the role that owns the tables. Used by migrations and by the isolation tests that
  must prove FORCE ROW LEVEL SECURITY applies even to the owner.
* `app_engine` — the role the application actually uses.

If either the deliberately-unprivileged path or the owner path could see another organization's
rows, isolation would be a story rather than a control.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

OWNER_URL = os.environ.get(
    "WORKOS_DATABASE_URL", "postgresql+psycopg://workos:workos@127.0.0.1:5432/workos_test"
)
APP_URL = os.environ.get(
    "WORKOS_APP_DATABASE_URL",
    "postgresql+psycopg://workos_app:workos_app@127.0.0.1:5432/workos_test",
)


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """Apply every migration from an empty schema, once per session."""
    env = {**os.environ, "WORKOS_DATABASE_URL": OWNER_URL, "WORKOS_APP_DATABASE_URL": APP_URL}
    owner = create_engine(OWNER_URL, future=True)
    with owner.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    owner.dispose()

    # Roles are environment bootstrap, not migration content, and they must exist before the
    # migrations run so that the conditional grants in 0001 actually land. In Compose the owner is
    # the image superuser; locally the owner needs CREATEROLE.
    owner = create_engine(OWNER_URL, future=True, isolation_level="AUTOCOMMIT")
    with owner.connect() as conn:
        conn.execute(text((REPO_ROOT / "ops" / "db" / "dev-roles.sql").read_text()))
    owner.dispose()

    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")

    yield OWNER_URL


@pytest.fixture(scope="session")
def owner_engine(migrated_database: str) -> Iterator[Engine]:
    engine = create_engine(migrated_database, future=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(migrated_database: str) -> Iterator[Engine]:
    engine = create_engine(APP_URL, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def owner_session(owner_engine: Engine) -> Iterator[Session]:
    maker = sessionmaker(bind=owner_engine, expire_on_commit=False, future=True)
    session = maker()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def app_session_factory(app_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=app_engine, expire_on_commit=False, future=True)


def _create_org(session: Session, slug: str) -> uuid.UUID:
    """Create an organization with the org context set to itself.

    Slightly odd-looking and correct: `organization` is subject to its own RLS policy, so a row can
    only be inserted by a session already claiming to be that organization.
    """
    org_id = uuid.uuid4()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": org_id, "name": slug.title(), "slug": slug},
    )
    return org_id


@pytest.fixture
def two_orgs(app_session_factory: sessionmaker[Session]) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """Two organizations with deliberately similar data, for isolation tests."""
    session = app_session_factory()
    org_a = _create_org(session, f"alpha-{uuid.uuid4().hex[:8]}")
    session.commit()
    session.close()

    session = app_session_factory()
    org_b = _create_org(session, f"beta-{uuid.uuid4().hex[:8]}")
    session.commit()
    session.close()

    for org_id, name in ((org_a, "Dana Alpha"), (org_b, "Dana Beta")):
        session = app_session_factory()
        session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
        )
        session.execute(
            text(
                "INSERT INTO person (org_id, display_name, email, keycloak_subject) "
                "VALUES (:org, :name, :email, :sub)"
            ),
            {
                "org": org_id,
                "name": name,
                "email": f"dana@{name.split()[1].lower()}.example",
                "sub": f"sub-{uuid.uuid4().hex[:8]}",
            },
        )
        session.commit()
        session.close()

    yield org_a, org_b
