"""Audit infrastructure.

Two properties matter and both are easy to lose later: an audit entry shares the transaction of the
change it describes, and history cannot be edited afterwards.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.platform.actor import Actor, ActorType, system_actor
from app.platform.audit import record_audit
from app.platform.authz import Action, Decision, ResourceType, Role
from app.platform.authz.model import Grant

pytestmark = pytest.mark.integration


def _scoped(session: Session, org_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )


def _person_actor(session: Session, org_id: uuid.UUID) -> Actor:
    person_id = session.execute(
        text("SELECT id FROM person WHERE org_id = :org LIMIT 1"), {"org": org_id}
    ).scalar()
    return Actor(type=ActorType.PERSON, person_id=person_id, request_id="req-test")


def test_an_audit_entry_is_written_and_readable_within_the_organization(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    actor = _person_actor(session, org_a)
    record = record_audit(
        session,
        org_id=org_a,
        actor=actor,
        action=Action.UPDATE,
        resource_type=ResourceType.PERSON,
        resource_id=actor.person_id,
        before={"display_name": "old"},
        after={"display_name": "new"},
        decision=Decision(True, "org_admin granted via org", Role.ORG_ADMIN, Grant.ORG),
    )
    session.commit()

    session = app_session_factory()
    _scoped(session, org_a)
    row = session.execute(
        text(
            "SELECT action, resource_type, before_state, after_state, request_id, "
            "authorization_context, actor FROM audit_entry WHERE id = :id"
        ),
        {"id": record.id},
    ).mappings().one()
    assert row["action"] == "update"
    assert row["resource_type"] == "person"
    assert row["before_state"] == {"display_name": "old"}
    assert row["after_state"] == {"display_name": "new"}
    assert row["request_id"] == "req-test"
    assert row["authorization_context"]["role"] == "org_admin"
    assert row["actor"]["type"] == "person"
    session.close()


def test_an_audit_entry_rolls_back_with_its_transaction(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """There is no audit record for something that did not happen (BR-G-02)."""
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    record = record_audit(
        session,
        org_id=org_a,
        actor=system_actor("rollback probe"),
        action=Action.CREATE,
        resource_type=ResourceType.TEAM,
        resource_id=uuid.uuid4(),
    )
    session.rollback()
    session.close()

    session = app_session_factory()
    _scoped(session, org_a)
    assert session.execute(
        text("SELECT count(*) FROM audit_entry WHERE id = :id"), {"id": record.id}
    ).scalar() == 0
    session.close()


def test_audit_entries_cannot_be_updated(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    record_audit(
        session,
        org_id=org_a,
        actor=system_actor("immutability probe"),
        action=Action.CREATE,
        resource_type=ResourceType.TEAM,
    )
    session.commit()

    session = app_session_factory()
    _scoped(session, org_a)
    with pytest.raises(DBAPIError):
        session.execute(text("UPDATE audit_entry SET action = 'tampered'"))
    session.rollback()
    session.close()


def test_audit_entries_cannot_be_deleted(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    with pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM audit_entry"))
    session.rollback()
    session.close()


def test_the_owner_role_also_cannot_rewrite_history(owner_session: Session) -> None:
    """The trigger, not a grant. Running as the owner is a mistake, not an escape hatch."""
    with pytest.raises(DBAPIError):
        owner_session.execute(text("DELETE FROM audit_entry"))
    owner_session.rollback()


def test_audit_entries_are_invisible_to_another_organization(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, org_b = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    record = record_audit(
        session,
        org_id=org_a,
        actor=system_actor("tenancy probe"),
        action=Action.CREATE,
        resource_type=ResourceType.TEAM,
    )
    session.commit()
    session.close()

    session = app_session_factory()
    _scoped(session, org_b)
    assert session.execute(
        text("SELECT count(*) FROM audit_entry WHERE id = :id"), {"id": record.id}
    ).scalar() == 0
    session.close()


def test_an_ai_actor_without_provenance_is_rejected_before_it_reaches_the_database() -> None:
    """BR-AI-02 and BR-AI-03, asserted now so the shape is fixed before Phase 3 needs it."""
    with pytest.raises(ValueError):
        Actor(type=ActorType.AI, person_id=uuid.uuid4())
    with pytest.raises(ValueError):
        Actor(type=ActorType.AI, ai_interaction_id=uuid.uuid4())
