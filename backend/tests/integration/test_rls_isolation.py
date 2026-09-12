"""Tenant isolation, proved at the database.

Every test here deliberately bypasses application-level scoping: no repository, no service, no
`org_session`. Raw SQL with an org context set, or not set at all. If isolation only holds because
some Python remembered to add a WHERE clause, it does not hold (PQ-2, contract §4).

The tests run twice over: once as the application role, once as the table owner. FORCE ROW LEVEL
SECURITY is what makes the second one pass, and it is the one that catches the classic mistake of
running production as the owner.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

SCOPED_QUERIES = {
    "direct read": "SELECT count(*) FROM person WHERE org_id = :other",
    "unfiltered list": "SELECT count(*) FROM person",
    "search": "SELECT count(*) FROM person WHERE display_name ILIKE '%dana%'",
    "aggregate": "SELECT count(DISTINCT org_id) FROM person",
    "organization row": "SELECT count(*) FROM organization",
    "audit query": "SELECT count(*) FROM audit_entry",
    "outbox query": "SELECT count(*) FROM outbox",
    "join traversal": (
        "SELECT count(*) FROM person p "
        "JOIN organization o ON o.id = p.org_id"
    ),
    "existence probe": "SELECT count(*) FROM person WHERE id IS NOT NULL",
    # The tables the Identity write side fills. `external_identity` matters most of the three:
    # it maps a phone number or a chat handle to a named person, so a leak across tenants is a
    # leak of who somebody is, not merely of what they were working on (ADR-0037).
    "external identity": "SELECT count(*) FROM external_identity",
    "external identity by handle": (
        "SELECT count(*) FROM external_identity WHERE external_id IS NOT NULL"
    ),
    "role assignment": "SELECT count(*) FROM role_assignment",
    "organization membership": "SELECT count(*) FROM organization_membership",
    "team membership": "SELECT count(*) FROM team_membership",
    "department": "SELECT count(*) FROM department",
    "team": "SELECT count(*) FROM team",
    # Signal/Capture. `event.body_text` is the captured content itself — a pasted conversation, a
    # meeting note — so a leak here is a leak of what was said, and `event_attachment` leaks the
    # object keys that a presigned URL would be issued against.
    "event": "SELECT count(*) FROM event",
    "event body text": "SELECT count(*) FROM event WHERE body_text IS NOT NULL",
    "event by other org": "SELECT count(*) FROM event WHERE org_id = :other",
    "event participant": "SELECT count(*) FROM event_participant",
    "event attachment": "SELECT count(*) FROM event_attachment",
    "attachment object keys": "SELECT count(*) FROM event_attachment WHERE object_key IS NOT NULL",
    "evidence": "SELECT count(*) FROM evidence",
    "capture join traversal": (
        "SELECT count(*) FROM event e "
        "JOIN event_participant p ON p.event_id = e.id "
        "JOIN event_attachment a ON a.event_id = e.id"
    ),
    "evidence back to its event": (
        "SELECT count(*) FROM evidence v JOIN event e ON e.id = v.event_id"
    ),
    # A join is where a forgotten policy hides: every table in the chain must carry its own.
    "identity join traversal": (
        "SELECT count(*) FROM external_identity e "
        "JOIN person p ON p.id = e.person_id "
        "JOIN organization_membership m ON m.person_id = p.id"
    ),
}


def _scoped(session: Session, org_id: uuid.UUID | None) -> None:
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(org_id) if org_id else ""},
    )


@pytest.fixture(params=["app_role", "owner_role"])
def role_factory(
    request: pytest.FixtureRequest, app_engine: Engine, owner_engine: Engine
) -> sessionmaker[Session]:
    engine = app_engine if request.param == "app_role" else owner_engine
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def test_neither_role_can_bypass_row_level_security(
    role_factory: sessionmaker[Session],
) -> None:
    """The precondition every other test here depends on.

    FORCE ROW LEVEL SECURITY is not enforced against a superuser or a role holding BYPASSRLS: the
    policies still exist, the planner simply ignores them. An owner with either attribute turns the
    rest of this module into a test of nothing, and the failure mode is silent — isolation looks
    proven right up until production runs as the wrong role. So assert the attribute, not just the
    behaviour.
    """
    session = role_factory()
    role, is_superuser, bypasses = session.execute(
        text(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user"
        )
    ).one()
    session.close()
    assert not is_superuser, (
        f"{role} is a superuser; FORCE ROW LEVEL SECURITY does not apply to it and the isolation "
        "proved below would be vacuous"
    )
    assert not bypasses, f"{role} holds BYPASSRLS; tenant policies are not enforced against it"


def test_an_unscoped_session_sees_nothing(
    role_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Default deny. A forgotten org context reads zero rows, not every row."""
    session = role_factory()
    _scoped(session, None)
    for label, query in SCOPED_QUERIES.items():
        count = session.execute(text(query), {"other": two_orgs[1]}).scalar()
        assert count == 0, f"unscoped session saw {count} rows via {label}"
    session.close()


def test_a_scoped_session_cannot_see_the_other_organization(
    role_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, org_b = two_orgs
    session = role_factory()
    _scoped(session, org_a)

    assert session.execute(
        text("SELECT count(*) FROM person WHERE org_id = :other"), {"other": org_b}
    ).scalar() == 0
    assert session.execute(
        text("SELECT count(DISTINCT org_id) FROM person")
    ).scalar() == 1
    assert session.execute(
        text("SELECT count(*) FROM organization WHERE id = :other"), {"other": org_b}
    ).scalar() == 0
    session.close()


def test_counts_and_aggregates_do_not_leak_the_other_organization(
    role_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Inference channel: a total that includes invisible rows is a leak with extra steps."""
    org_a, _ = two_orgs
    session = role_factory()
    _scoped(session, org_a)
    visible = session.execute(text("SELECT count(*) FROM person")).scalar()
    own = session.execute(
        text("SELECT count(*) FROM person WHERE org_id = :own"), {"own": org_a}
    ).scalar()
    assert visible == own
    session.close()


def test_a_row_cannot_be_written_into_another_organization(
    role_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """WITH CHECK. Isolation that only covers reads is half a control."""
    org_a, org_b = two_orgs
    session = role_factory()
    _scoped(session, org_a)
    with pytest.raises(DBAPIError):
        session.execute(
            text("INSERT INTO person (org_id, display_name) VALUES (:org, 'intruder')"),
            {"org": org_b},
        )
    session.rollback()
    session.close()


def test_an_update_cannot_move_a_row_across_organizations(
    role_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, org_b = two_orgs
    session = role_factory()
    _scoped(session, org_a)
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE person SET org_id = :other WHERE org_id = :own"),
            {"other": org_b, "own": org_a},
        )
    session.rollback()
    session.close()


def test_a_cross_organization_foreign_key_is_rejected_by_the_database(
    owner_engine: Engine, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Composite foreign keys (migration 0002). Not a service check that someone can forget."""
    org_a, org_b = two_orgs
    maker = sessionmaker(bind=owner_engine, expire_on_commit=False, future=True)
    session = maker()
    _scoped(session, org_b)
    stranger = session.execute(
        text("SELECT id FROM person WHERE org_id = :org LIMIT 1"), {"org": org_b}
    ).scalar()
    session.rollback()

    session = maker()
    _scoped(session, org_a)
    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO team (org_id, name, lead_person_id) "
                "VALUES (:org, 'borrowed lead', :person)"
            ),
            {"org": org_a, "person": stranger},
        )
    session.rollback()
    session.close()


def test_every_registered_table_has_rls_enabled_and_forced(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.table_name, c.relrowsecurity, c.relforcerowsecurity
                FROM tenant_scoped_table t
                JOIN pg_class c ON c.relname = t.table_name
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                """
            )
        ).all()
    assert rows, "the tenant-scoped table registry is empty"
    for name, enabled, forced in rows:
        assert enabled, f"{name} does not have row-level security enabled"
        assert forced, f"{name} does not force row-level security, so the owner bypasses it"
