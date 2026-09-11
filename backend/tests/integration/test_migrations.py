"""Migration hygiene.

A migration that cannot be applied to an empty database, or that has drifted from the models, is a
production incident waiting for a deployment window.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from sqlalchemy import Engine, create_engine, inspect, text

from tests.conftest import APP_URL, BACKEND_ROOT, OWNER_URL

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "alembic_version",
    "audit_entry",
    "department",
    "external_identity",
    "organization",
    "organization_membership",
    "outbox",
    "person",
    "role_assignment",
    "team",
    "team_membership",
    "tenant_scoped_table",
}


def _alembic(*args: str, url: str = OWNER_URL) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "WORKOS_DATABASE_URL": url, "WORKOS_APP_DATABASE_URL": APP_URL}
    return subprocess.run(
        ["alembic", *args], cwd=BACKEND_ROOT, env=env, capture_output=True, text=True
    )


def test_clean_database_migrates_to_head(owner_engine: Engine) -> None:
    tables = set(inspect(owner_engine).get_table_names())
    assert tables >= EXPECTED_TABLES, f"missing tables: {sorted(EXPECTED_TABLES - tables)}"


def test_migration_revisions_are_sequential_and_linear() -> None:
    result = _alembic("history")
    assert result.returncode == 0, result.stderr
    # A branch would show up here as more than one head, and branching migrations are a mess to
    # reason about in a single-database monolith.
    heads = _alembic("heads")
    assert len([line for line in heads.stdout.splitlines() if line.strip()]) == 1


def test_models_have_not_drifted_from_the_schema(owner_engine: Engine) -> None:
    """Every mapped table and column must exist in the database with the same nullability."""
    import app.contexts.identity.models  # noqa: F401
    from app.platform.db import Base

    inspector = inspect(owner_engine)
    db_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        assert table.name in db_tables, f"model {table.name} has no table"
        db_columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            assert column.name in db_columns, f"{table.name}.{column.name} missing from the \
                database"
            assert db_columns[column.name]["nullable"] == column.nullable, (
                f"{table.name}.{column.name} nullability differs between model and schema"
            )


def test_downgrade_then_upgrade_returns_to_the_same_schema(owner_engine: Engine) -> None:
    """Rollback is not decoration. If down() is wrong, the first bad deploy is unrecoverable."""
    before = set(inspect(owner_engine).get_table_names())

    down = _alembic("downgrade", "base")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"
    with create_engine(OWNER_URL, future=True).connect() as conn:
        remaining = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
            )
        ).scalars().all()
    assert remaining == [], f"downgrade left tables behind: {remaining}"

    up = _alembic("upgrade", "head")
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"
    assert set(inspect(owner_engine).get_table_names()) == before


def test_the_org_context_function_exists_and_defaults_to_null(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT app_current_org()")).scalar() is None
