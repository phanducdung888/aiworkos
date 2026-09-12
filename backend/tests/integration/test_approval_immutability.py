"""Proposal and ApprovalRecord immutability, proved at the database.

Raw SQL as the table owner, past every service. An ApprovalRecord is the record binding a person's
authority to one exact mutation; if a maintenance script can edit it, then what a human approved is
whatever the last writer said it was, and the audit trail describes a decision nobody made.

The mutable sets are asserted as carefully as the frozen ones. Both rows have to keep moving — a
Proposal is decided, an approval is executed — so a trigger that refused every update would be easy
to write, would pass a test that only checked refusals, and would make both entities unusable.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.platform.ids import uuid7

pytestmark = pytest.mark.integration

#: What a reviewer read before deciding. None of it may change afterwards (ADR-0041).
PROPOSAL_FROZEN = {
    "kind": "'update'",
    "target_type": "'commitment'",
    "summary": "'something else entirely'",
    "action": "'{\"tool\": \"other\"}'::jsonb",
    "action_hash": "'forged'",
    "confidence": "1",
    "routed_to_person_id": "gen_random_uuid()",
    "expires_at": "now() + interval '900 days'",
    "org_id": "gen_random_uuid()",
}

#: Deciding a Proposal, as one coherent write.
#:
#: The columns are not listed individually because `proposal_decision_is_attributed` refuses a
#: status change that does not say who made it — which is the constraint working, and is why a
#: reviewer can never find a decided Proposal with no decider on it.
PROPOSAL_DECISION = (
    "status = 'rejected', reviewed_by_person_id = :person, "
    "reviewed_at = now(), rejection_reason = 'no'"
)

#: The approval itself. The execution outcome is separate and writable once.
APPROVAL_FROZEN = {
    "decision": "'rejected'",
    "approved_action": "'{\"tool\": \"other\"}'::jsonb",
    "approved_action_hash": "'forged'",
    "approver_person_id": "gen_random_uuid()",
    "edits": "'{}'::jsonb",
    "decided_at": "now() - interval '10 days'",
    "org_id": "gen_random_uuid()",
}

APPROVAL_MUTABLE = {
    "execution_status": "'failed'",
    "execution_error": "'boom'",
    "resulting_entity_type": "'work'",
}


@dataclasses.dataclass(frozen=True)
class Fixture:
    org_id: uuid.UUID
    person_id: uuid.UUID
    proposal_id: uuid.UUID
    approval_id: uuid.UUID


@pytest.fixture
def a_decision(owner_session: Session) -> Iterator[Fixture]:
    """One organization, one person, one proposal, one approval of it.

    Session-level org context, cleared on the way out: these tests commit repeatedly and a
    transaction-local setting would be discarded at the first commit, hiding the fixture's own rows
    and turning a refusal that never happened into a passing test.
    """
    org_id, person_id = uuid7(), uuid7()
    proposal_id, approval_id = uuid7(), uuid7()
    owner_session.execute(
        text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(org_id)}
    )
    owner_session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Imm', :slug)"),
        {"id": org_id, "slug": f"imm-{uuid.uuid4().hex[:12]}"},
    )
    owner_session.execute(
        text(
            "INSERT INTO person (id, org_id, display_name, status) "
            "VALUES (:id, :org, 'Approver', 'active')"
        ),
        {"id": person_id, "org": org_id},
    )
    owner_session.execute(
        text(
            "INSERT INTO proposal (id, org_id, kind, target_type, summary, action, action_hash, "
            "routed_to_person_id, expires_at) VALUES (:id, :org, 'create', 'work', 'a summary', "
            "'{\"tool\": \"create_work\"}'::jsonb, 'h1', :person, now() + interval '14 days')"
        ),
        {"id": proposal_id, "org": org_id, "person": person_id},
    )
    owner_session.execute(
        text(
            "INSERT INTO approval_record (id, org_id, proposal_id, approver_person_id, decision, "
            "approved_action, approved_action_hash) VALUES (:id, :org, :proposal, :person, "
            "'approved', '{\"tool\": \"create_work\"}'::jsonb, 'h1')"
        ),
        {"id": approval_id, "org": org_id, "proposal": proposal_id, "person": person_id},
    )
    owner_session.commit()
    try:
        yield Fixture(
            org_id=org_id,
            person_id=person_id,
            proposal_id=proposal_id,
            approval_id=approval_id,
        )
    finally:
        owner_session.rollback()
        owner_session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
        owner_session.commit()


@pytest.mark.parametrize(("column", "value"), sorted(PROPOSAL_FROZEN.items()))
def test_what_a_reviewer_read_cannot_change(
    owner_session: Session, a_decision: Fixture, column: str, value: str
) -> None:
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(
            text(f"UPDATE proposal SET {column} = {value} WHERE id = :id"),
            {"id": a_decision.proposal_id},
        )
    assert "immutable" in str(error.value).lower()
    owner_session.rollback()


def test_a_proposal_can_still_be_decided(
    owner_session: Session, a_decision: Fixture
) -> None:
    """The other half of immutability: the row has to keep moving."""
    owner_session.execute(
        text(f"UPDATE proposal SET {PROPOSAL_DECISION} WHERE id = :id"),
        {"id": a_decision.proposal_id, "person": a_decision.person_id},
    )
    owner_session.rollback()


def test_a_status_change_must_say_who_made_it(
    owner_session: Session, a_decision: Fixture
) -> None:
    """A decided Proposal with no decider is not a state this schema can be left in."""
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(
            text("UPDATE proposal SET status = 'rejected' WHERE id = :id"),
            {"id": a_decision.proposal_id},
        )
    assert "proposal_decision_is_attributed" in str(error.value)
    owner_session.rollback()


@pytest.mark.parametrize(("column", "value"), sorted(APPROVAL_FROZEN.items()))
def test_an_approval_cannot_be_rewritten(
    owner_session: Session, a_decision: Fixture, column: str, value: str
) -> None:
    """Including the action and its hash — the two things execution matches on."""
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(
            text(f"UPDATE approval_record SET {column} = {value} WHERE id = :id"),
            {"id": a_decision.approval_id},
        )
    assert "immutable" in str(error.value).lower()
    owner_session.rollback()


@pytest.mark.parametrize(("column", "value"), sorted(APPROVAL_MUTABLE.items()))
def test_the_execution_outcome_is_writable(
    owner_session: Session, a_decision: Fixture, column: str, value: str
) -> None:
    owner_session.execute(
        text(f"UPDATE approval_record SET {column} = {value} WHERE id = :id"),
        {"id": a_decision.approval_id},
    )
    owner_session.rollback()


@pytest.mark.parametrize("table", ["proposal", "approval_record"])
def test_neither_can_be_deleted(
    owner_session: Session, a_decision: Fixture, table: str
) -> None:
    identifier = (
        a_decision.proposal_id if table == "proposal" else a_decision.approval_id
    )
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(
            text(f"DELETE FROM {table} WHERE id = :id"), {"id": identifier}
        )
    assert "immutable" in str(error.value).lower()
    owner_session.rollback()


@pytest.mark.parametrize("table", ["proposal", "approval_record"])
def test_neither_can_be_truncated(
    owner_session: Session, a_decision: Fixture, table: str
) -> None:
    """A row trigger never fires for TRUNCATE; the table would empty in silence without this."""
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(text(f"TRUNCATE {table} CASCADE"))
    assert "truncate" in str(error.value).lower()
    owner_session.rollback()


@pytest.mark.parametrize("table", ["proposal", "approval_record"])
def test_the_trigger_freezes_by_subtraction(owner_session: Session, table: str) -> None:
    """A column added by a later migration is frozen by default (ADR-0038's mechanism).

    Asserted against the installed function rather than by adding a column: an `ALTER TABLE` here
    takes an ACCESS EXCLUSIVE lock and deadlocks against the sessions this suite keeps open.
    """
    body = owner_session.execute(
        text("SELECT prosrc FROM pg_proc WHERE proname = :name"),
        {"name": f"{table}_is_immutable"},
    ).scalar_one()
    assert "to_jsonb(OLD)" in body and "to_jsonb(NEW)" in body, (
        "the trigger must compare whole rows; naming columns silently permits any field added later"
    )
    frozen = PROPOSAL_FROZEN if table == "proposal" else APPROVAL_FROZEN
    named = {column for column in frozen if f"'{column}'" in body}
    assert not named, f"frozen columns must not be enumerated: {sorted(named)}"
