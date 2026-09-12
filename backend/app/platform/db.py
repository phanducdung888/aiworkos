"""Database access boundary.

This is the only module that creates engines or sessions. Repositories import from here;
routers, tools and the future agent runtime never do.

The organization context is set per transaction with `set_config(..., is_local => true)` so it
cannot leak across pooled connections. Row-Level Security reads that setting, which is why every
statement must run inside `org_session` (or `admin_session` for deliberately unscoped work).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.platform.config import get_settings

ORG_SETTING = "app.current_org_id"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata


_engines: dict[str, Engine] = {}


def get_engine(url: str | None = None, *, admin: bool = False) -> Engine:
    settings = get_settings()
    resolved = url or (settings.database_url if admin else settings.app_database_url)
    if resolved not in _engines:
        _engines[resolved] = create_engine(
            resolved, echo=settings.sql_echo, pool_pre_ping=True, future=True
        )
    return _engines[resolved]


def session_factory(url: str | None = None, *, admin: bool = False) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url, admin=admin), expire_on_commit=False, future=True)


def set_org_context(session: Session, org_id: uuid.UUID | None) -> None:
    """Bind the current transaction to one organization. `None` clears the binding."""
    session.execute(
        text("SELECT set_config(:key, :value, true)"),
        {"key": ORG_SETTING, "value": str(org_id) if org_id else ""},
    )


def current_org_context(session: Session) -> uuid.UUID | None:
    raw = session.execute(
        text("SELECT current_setting(:key, true)"), {"key": ORG_SETTING}
    ).scalar()
    return uuid.UUID(raw) if raw else None


@contextmanager
def org_session(
    org_id: uuid.UUID, factory: sessionmaker[Session] | None = None
) -> Iterator[Session]:
    """A transaction scoped to one organization. Commits on success, rolls back on failure."""
    maker = factory or session_factory()
    session = maker()
    try:
        set_org_context(session, org_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def admin_session(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """An unscoped transaction, for migrations and ops only.

    Deliberately separate and deliberately awkward to reach: application code that finds itself
    wanting this is usually about to write a cross-tenant query.
    """
    maker = factory or session_factory(admin=True)
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@lru_cache
def worker_engine() -> Engine:
    """The worker's connection, as `workos_worker` (ADR-0046).

    Separate from the application engine on purpose: the queue exemption lives on this role, and a
    shared pool would mean an HTTP request could be served by a connection that holds it.
    """
    settings = get_settings()
    return create_engine(
        settings.worker_database_url, echo=settings.sql_echo, future=True, pool_pre_ping=True
    )


def worker_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=worker_engine(), expire_on_commit=False, future=True)
