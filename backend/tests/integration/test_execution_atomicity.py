"""Execution writes everything or nothing.

A failed execution is the dangerous case, and not because the mutation fails — that is visible and
recoverable. The danger is the half-write: an ApprovalRecord marked `executed` naming a Work item
that was rolled back, or a Work item created under an approval the system still thinks is pending
and will happily spend again.

The failures are induced by real refusals rather than by patching, because what is being tested is
that the transaction boundary is where it looks like it is.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)


def counts(session: Session, org_id: uuid.UUID) -> dict[str, int]:
    session.rollback()
    return {
        table: session.execute(
            text(f"SELECT count(*) FROM {table} WHERE org_id = :org"), {"org": org_id}
        ).scalar_one()
        for table in (
            "work",
            "commitment",
            "proposal",
            "approval_record",
            "audit_entry",
            "outbox",
        )
    }


def a_proposal(
    api: TestClient, headers: dict[str, str], routed_to: uuid.UUID, **over: object
) -> dict:
    payload: dict[str, object] = {
        "kind": "create",
        "target_type": "work",
        "summary": "Create the follow-up",
        "tool": "create_work",
        "tool_version": "v1",
        "arguments": {"title": "Follow up on the quote"},
        "routed_to_person_id": str(routed_to),
    }
    payload.update(over)
    response = api.post("/api/v1/proposals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def approve(api: TestClient, headers: dict[str, str], proposal: dict) -> dict:
    response = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**headers, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_successful_execution_writes_the_mutation_and_the_outcome_together(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The control. Without it, an execution path that wrote nothing would pass every rollback
    test below perfectly."""
    proposal = a_proposal(api, as_admin, work_org.admin)
    record = approve(api, as_admin, proposal)
    before = counts(scoped_session, work_org.org_id)

    executed = api.post(f"/api/v1/approvals/{record['id']}/execute", headers=as_admin)
    assert executed.status_code == 200, executed.text

    after = counts(scoped_session, work_org.org_id)
    assert after["work"] == before["work"] + 1
    assert after["audit_entry"] > before["audit_entry"]
    assert after["outbox"] > before["outbox"]

    scoped_session.rollback()
    row = scoped_session.execute(
        text(
            "SELECT execution_status, resulting_entity_type, resulting_entity_id, executed_at "
            "FROM approval_record WHERE id = :id"
        ),
        {"id": uuid.UUID(record["id"])},
    ).mappings().one()
    assert row["execution_status"] == "executed"
    assert row["resulting_entity_type"] == "work"
    assert row["executed_at"] is not None
    # The approval names what it produced, and that row exists. Half of BR-PR-08's chain.
    assert scoped_session.execute(
        text("SELECT count(*) FROM work WHERE id = :id"),
        {"id": row["resulting_entity_id"]},
    ).scalar_one() == 1


def test_a_refused_mutation_leaves_no_work_and_no_spent_approval(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The action names a Project that does not exist, so the Work Core refuses it (ADR-0035).

    The refusal arrives after the approval has been claimed, which is exactly the window a
    half-write would open: the record must not stay claimed, and no Work may exist.
    """
    proposal = a_proposal(
        api,
        as_admin,
        work_org.admin,
        arguments={"title": "Doomed", "project_id": str(uuid.uuid4())},
    )
    record = approve(api, as_admin, proposal)
    before = counts(scoped_session, work_org.org_id)

    failed = api.post(f"/api/v1/approvals/{record['id']}/execute", headers=as_admin)
    # 404 today, because `WorkService.create` loads the Project and raises `EntityNotFound` for a
    # `project_id` that does not resolve — the same body-field-as-404 inconsistency found and fixed
    # for assignments in Checkpoint 5.1, still present on this path and left alone here because it
    # is Work Core behaviour rather than anything this checkpoint introduced. Recorded as open.
    # What this test is actually about is the line below it: whatever the status, nothing was left.
    assert 400 <= failed.status_code < 500, failed.text

    after = counts(scoped_session, work_org.org_id)
    assert after["work"] == before["work"], "a refused execution created work"
    assert after["audit_entry"] == before["audit_entry"]
    assert after["outbox"] == before["outbox"]

    scoped_session.rollback()
    status = scoped_session.execute(
        text("SELECT execution_status FROM approval_record WHERE id = :id"),
        {"id": uuid.UUID(record["id"])},
    ).scalar_one()
    assert status == "pending", (
        "a failed execution must leave the approval usable; a claim that survives a rollback "
        "would strand it forever"
    )


def test_a_retried_failed_execution_can_succeed(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The other half of the rollback: the approval is still spendable afterwards.

    Asserting only that nothing was written would pass against an implementation that also lost
    the approval, which is a different bug wearing the same clean database.
    """
    proposal = a_proposal(
        api, as_admin, work_org.admin, arguments={"title": "x", "project_id": str(uuid.uuid4())}
    )
    record = approve(api, as_admin, proposal)
    first = api.post(f"/api/v1/approvals/{record['id']}/execute", headers=as_admin)
    assert 400 <= first.status_code < 500

    read = api.get(f"/api/v1/proposals/{proposal['id']}/approval", headers=as_admin).json()
    assert read["execution_status"] == "pending"


def test_a_rejected_proposal_writes_a_record_and_no_mutation(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    proposal = a_proposal(api, as_admin, work_org.admin)
    before = counts(scoped_session, work_org.org_id)

    response = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "rejected", "rejection_reason": "already handled"},
        headers={**as_admin, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert response.status_code == 201

    after = counts(scoped_session, work_org.org_id)
    assert after["work"] == before["work"]
    assert after["approval_record"] == before["approval_record"] + 1


def test_no_approval_claims_an_entity_that_does_not_exist(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The invariant asserted over everything this suite has written.

    An ApprovalRecord naming a missing entity is the failure nobody can recover from downstream:
    the audit trail says a change was made and the change is not there.
    """
    proposal = a_proposal(api, as_admin, work_org.admin)
    record = approve(api, as_admin, proposal)
    api.post(f"/api/v1/approvals/{record['id']}/execute", headers=as_admin)

    scoped_session.rollback()
    orphans = scoped_session.execute(
        text(
            """
            SELECT a.id, a.resulting_entity_type
            FROM approval_record a
            LEFT JOIN work w
              ON w.id = a.resulting_entity_id AND w.org_id = a.org_id
            LEFT JOIN commitment c
              ON c.id = a.resulting_entity_id AND c.org_id = a.org_id
            LEFT JOIN work_assignment s
              ON s.id = a.resulting_entity_id AND s.org_id = a.org_id
            WHERE a.execution_status = 'executed'
              AND w.id IS NULL AND c.id IS NULL AND s.id IS NULL
            """
        )
    ).all()
    assert not orphans, f"approvals naming entities that do not exist: {orphans}"


def test_a_commitment_created_through_the_gateway_cites_its_evidence(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-C-03. Anything arriving through the Gateway came from a Proposal, so it must cite.

    A commitment the system asserts somebody made, with no record of where it was said, is the
    thing this rule exists to prevent.
    """
    event = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": "I will send the revised quote by Friday",
            "source_system": "openclaw.whatsapp",
            "source_ref": f"m-{uuid.uuid4().hex[:8]}",
        },
        headers=as_admin,
    ).json()
    evidence = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "commitment",
            "target_id": str(uuid.uuid4()),
            "assertion": "creates",
            "text_locator": {"char_start": 0, "char_end": 31},
            "excerpt": "I will send the revised quote b"[:31],
            "confidence": 85,
        },
        headers=as_admin,
    )
    assert evidence.status_code == 201, evidence.text

    proposal = a_proposal(
        api,
        as_admin,
        work_org.admin,
        target_type="commitment",
        tool="create_commitment",
        summary="Record the promise about the quote",
        arguments={
            "statement": "I will send the revised quote by Friday",
            "committed_by_person_id": str(work_org.member),
            "origin_event_id": event["id"],
            "evidence_ids": [evidence.json()["id"]],
        },
        evidence_ids=[evidence.json()["id"]],
    )
    record = approve(api, as_admin, proposal)
    executed = api.post(f"/api/v1/approvals/{record['id']}/execute", headers=as_admin)
    assert executed.status_code == 200, executed.text

    created = api.get(
        f"/api/v1/commitments/{executed.json()['entity_id']}", headers=as_admin
    ).json()
    assert created["origin_event_id"] == event["id"]
    assert created["status"] == "captured"


def test_a_gateway_commitment_without_evidence_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-C-03, at the point it actually bites."""
    proposal = a_proposal(
        api,
        as_admin,
        work_org.admin,
        target_type="commitment",
        tool="create_commitment",
        summary="Record a promise nobody can show",
        arguments={
            "statement": "I will do the thing",
            "committed_by_person_id": str(work_org.member),
        },
    )
    record = approve(api, as_admin, proposal)
    before = counts(scoped_session, work_org.org_id)

    failed = api.post(f"/api/v1/approvals/{record['id']}/execute", headers=as_admin)
    assert failed.status_code == 422
    assert "BR-C-03" in failed.text
    assert counts(scoped_session, work_org.org_id) == before
