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
    # Commitment, Proposal and ApprovalRecord (CP7). A leaked `approval_record` is worse than a
    # leaked row of business data: it is the record of who authorised what, which is the thing an
    # auditor reads and the thing a forger would want to write.
    "commitment": "SELECT count(*) FROM commitment",
    "commitment statements": "SELECT count(*) FROM commitment WHERE statement IS NOT NULL",
    "proposal": "SELECT count(*) FROM proposal",
    "proposal actions": "SELECT count(*) FROM proposal WHERE action IS NOT NULL",
    "proposal evidence": "SELECT count(*) FROM proposal_evidence",
    "proposed change": "SELECT count(*) FROM proposed_change",
    "approval record": "SELECT count(*) FROM approval_record",
    "approved action hashes": (
        "SELECT count(*) FROM approval_record WHERE approved_action_hash IS NOT NULL"
    ),
    # The provenance chain itself must not cross a tenant boundary at any hop (BR-PR-08).
    "provenance chain traversal": (
        "SELECT count(*) FROM approval_record a "
        "JOIN proposal p ON p.id = a.proposal_id "
        "JOIN proposal_evidence pe ON pe.proposal_id = p.id "
        "JOIN evidence v ON v.id = pe.evidence_id "
        "JOIN event e ON e.id = v.event_id"
    ),
    # The AI layer (CP8). `ai_interaction` names which human's authority a run borrowed, and
    # `tool_call` records what it tried — including what it was refused. Both are audit surfaces,
    # so a cross-tenant read here is a read of another organization's oversight.
    # Since ADR-0046 the queue is an ordinary tenant table for every role but the worker,
    # so it belongs in the sweep that proves default-deny.
    "job queue": "SELECT count(*) FROM job",
    "job payloads": "SELECT count(*) FROM job WHERE payload IS NOT NULL",
    "agent capability policy": "SELECT count(*) FROM agent_capability_policy",
    "ai interaction": "SELECT count(*) FROM ai_interaction",
    "ai interaction by principal": (
        "SELECT count(*) FROM ai_interaction WHERE principal_person_id IS NOT NULL"
    ),
    "tool call": "SELECT count(*) FROM tool_call",
    "denied tool calls": (
        "SELECT count(*) FROM tool_call WHERE authorization_result = 'denied'"
    ),
    "ai provenance traversal": (
        "SELECT count(*) FROM proposal p "
        "JOIN ai_interaction i ON i.id = p.ai_interaction_id "
        "JOIN person pe ON pe.id = i.principal_person_id"
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


def test_no_table_is_readable_without_an_organization_context(owner_engine: Engine) -> None:
    """ADR-0046 closed the Checkpoint 8 exception, and this is what keeps it closed.

    That exception was keyed on *the absence of a setting*, so any session that forgot to scope —
    including one serving an HTTP request — could read the queue. A condition that grants access
    when a variable is unset is the opposite of default-deny, and the fix was to key the worker's
    exemption on identity instead. No policy anywhere may test for a missing organization again.
    """
    with owner_engine.connect() as conn:
        unscoped = set(
            conn.execute(
                text(
                    """
                    SELECT DISTINCT tablename FROM pg_policies
                    WHERE schemaname = 'public'
                      AND (qual LIKE '%app_current_org() IS NULL%'
                           OR with_check LIKE '%app_current_org() IS NULL%')
                    """
                )
            ).scalars()
        )
    assert not unscoped, (
        f"policies granting access when no organization is set: {sorted(unscoped)}; the exemption "
        "must be an identity, not a missing setting (ADR-0046)"
    )


def test_only_the_worker_role_is_exempt_and_only_on_the_queue(owner_engine: Engine) -> None:
    """The exemption exists, is one role, and reaches one table.

    A second table naming `workos_worker` would be a second place the worker can see across
    tenants, which is the kind of thing that spreads one convenient grant at a time.
    """
    with owner_engine.connect() as conn:
        exempt = set(
            conn.execute(
                text(
                    """
                    SELECT DISTINCT tablename FROM pg_policies
                    WHERE schemaname = 'public' AND qual LIKE '%workos_worker%'
                    """
                )
            ).scalars()
        )
    assert exempt == {"job"}, (
        f"tables the worker role can reach across tenants: {sorted(exempt)}; only the queue may be"
    )


def test_the_application_role_cannot_read_the_queue_unscoped(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The regression Checkpoint 9 exists to prevent.

    The application role serves every HTTP request and is the one an attacker reaches. Before
    ADR-0046 a forgotten `set_config` handed it the whole queue.
    """
    session = app_session_factory()
    _scoped(session, None)
    assert session.execute(text("SELECT count(*) FROM job")).scalar() == 0
    session.close()


def test_the_worker_role_can_claim_across_tenants(
    worker_engine: Engine, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The other half: the exemption has to actually work, or the worker cannot function.

    A test that only asserted the refusals would pass against a database where the worker is as
    blind as everybody else, and the queue would simply never drain.
    """
    factory = sessionmaker(bind=worker_engine, expire_on_commit=False, future=True)
    session = factory()
    _scoped(session, None)
    for org_id in two_orgs:
        session.execute(
            text(
                "INSERT INTO job (id, org_id, kind, payload) "
                "VALUES (gen_random_uuid(), :org, 'execute_approval', '{}'::jsonb)"
            ),
            {"org": org_id},
        )
    session.commit()

    _scoped(session, None)
    visible = session.execute(
        text("SELECT count(DISTINCT org_id) FROM job WHERE org_id = ANY(:orgs)"),
        {"orgs": list(two_orgs)},
    ).scalar()
    assert visible == 2, "the worker must see work in every tenant, or the queue never drains"
    session.rollback()
    session.close()


def test_the_worker_role_is_not_exempt_on_business_tables(
    worker_engine: Engine, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """One policy on one table. Everywhere else the worker is as constrained as the app role.

    This is what makes "the worker scoped itself correctly" a property the database enforces
    rather than something the loop remembers to do.
    """
    factory = sessionmaker(bind=worker_engine, expire_on_commit=False, future=True)
    session = factory()
    _scoped(session, None)
    for table in ("work", "event", "proposal", "approval_record", "person", "commitment"):
        count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        assert count == 0, f"the worker saw {count} rows of {table} with no organization set"
    session.close()


def test_enqueueing_still_requires_an_organization(
    role_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The half of the exception that is not relaxed.

    A worker may *read* the queue unscoped. Nothing may write to it unscoped, or a compromised
    worker could queue mutations against any tenant it named.
    """
    session = role_factory()
    _scoped(session, None)
    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO job (id, org_id, kind, payload) "
                "VALUES (gen_random_uuid(), :org, 'execute_approval', '{}'::jsonb)"
            ),
            {"org": two_orgs[0]},
        )
    session.rollback()
    session.close()
