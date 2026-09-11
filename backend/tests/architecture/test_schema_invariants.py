"""Schema invariants from the approved decisions.

Several of these assert the absence of something that does not exist yet. That is the point: they
are cheap now and they fail the moment a future checkpoint reintroduces a shape an ADR ruled out.
A test written after the mistake is a post-mortem, not a control.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text

pytestmark = [pytest.mark.architecture, pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "backend" / "migrations" / "versions"

# Tables that legitimately carry no org_id. Everything else must be tenant-scoped (BR-G-01).
UNSCOPED_BY_DESIGN = {"alembic_version", "tenant_scoped_table"}


def test_every_table_is_either_tenant_scoped_or_explicitly_exempt(owner_engine: Engine) -> None:
    inspector = inspect(owner_engine)
    for table in inspector.get_table_names():
        if table in UNSCOPED_BY_DESIGN:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        scoping_column = "id" if table == "organization" else "org_id"
        assert scoping_column in columns, f"{table} has no organization scoping column"


def test_every_tenant_scoped_table_is_in_the_registry(owner_engine: Engine) -> None:
    """Adding a scoped table without registering it should fail loudly, not silently skip RLS."""
    inspector = inspect(owner_engine)
    with owner_engine.connect() as conn:
        registered = set(conn.execute(text("SELECT table_name FROM tenant_scoped_table")).scalars())
    actual = {
        t
        for t in inspector.get_table_names()
        if t not in UNSCOPED_BY_DESIGN
    }
    assert actual == registered, (
        f"registry drift — unregistered: {sorted(actual - registered)}, "
        f"stale: {sorted(registered - actual)}"
    )


def test_every_tenant_scoped_table_has_a_policy(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.table_name, count(p.policyname) AS policies
                FROM tenant_scoped_table t
                LEFT JOIN pg_policies p
                  ON p.tablename = t.table_name AND p.schemaname = 'public'
                GROUP BY t.table_name
                """
            )
        ).all()
    for name, policies in rows:
        assert policies > 0, f"{name} is registered as tenant-scoped but has no RLS policy"


def test_work_has_no_assignee_column(owner_engine: Engine) -> None:
    """ADR-0032. WorkAssignment is canonical; a second writable owner column is forbidden."""
    inspector = inspect(owner_engine)
    if "work" not in inspector.get_table_names():
        pytest.skip("work arrives in a later checkpoint; the invariant is asserted in advance")
    columns = {c["name"] for c in inspector.get_columns("work")}
    forbidden = {"assignee_person_id", "owner_person_id", "assignee_id", "collaborator_person_ids"}
    assert not (columns & forbidden), f"work carries a duplicate assignment field: {columns & \
        forbidden}"


def test_work_project_id_is_nullable(owner_engine: Engine) -> None:
    """ADR-0029. Work without a Project is first-class."""
    inspector = inspect(owner_engine)
    if "work" not in inspector.get_table_names():
        pytest.skip("work arrives in a later checkpoint; the invariant is asserted in advance")
    project_id = next(c for c in inspector.get_columns("work") if c["name"] == "project_id")
    assert project_id["nullable"], "work.project_id must be nullable"


def test_there_is_no_comment_table(owner_engine: Engine) -> None:
    """ADR-0033. Comments are Events, not an aggregate."""
    assert "comment" not in inspect(owner_engine).get_table_names()


def test_no_migration_creates_a_synthetic_project() -> None:
    """BR-P-07a. No General, Default, Unassigned or Inbox Project, by any route."""
    banned = ("general project", "default project", "unassigned project", "inbox project")
    for migration in MIGRATIONS.glob("*.py"):
        lowered = migration.read_text().lower()
        if "insert into project" not in lowered:
            continue
        for phrase in banned:
            assert phrase not in lowered, f"{migration.name} seeds a synthetic project"


def test_migration_filenames_are_sequential() -> None:
    revisions = sorted(p.name.split("_")[0] for p in MIGRATIONS.glob("*.py"))
    assert revisions == [f"{i + 1:04d}" for i in range(len(revisions))], (
        f"migration numbering is not sequential: {revisions}"
    )


def test_the_audit_table_is_append_only_at_the_database_level(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        triggers = conn.execute(
            text(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_entry'::regclass "
                "AND NOT tgisinternal"
            )
        ).scalars().all()
        policies = conn.execute(
            text(
                "SELECT cmd FROM pg_policies WHERE tablename = 'audit_entry' AND schemaname = \
                    'public'"
            )
        ).scalars().all()
    assert "trg_audit_entry_append_only" in triggers
    assert set(policies) <= {"SELECT", "INSERT"}, (
        f"audit_entry has policies permitting {set(policies)}; it must be append-only"
    )
