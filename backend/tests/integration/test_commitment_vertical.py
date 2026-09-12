"""The loop this checkpoint exists to make reachable, end to end.

Confirmed external identity → Event → AI analysis → commitment signal → Evidence → ToolIntent →
Proposal → human approval → queue → worker → Tool Gateway → Commitment.

Every piece of this existed before CP14 and the whole never ran, because nothing mapped a channel
handle to a Person. `test_agent_flow` proves the analysis half against Events whose participants a
caller had already resolved by hand; this file starts from a phone number, which is what actually
arrives.

The conservative branches matter as much as the happy path and are asserted here rather than
described: no speaker at all degrades to Work, and a speaker nobody has vouched for produces
nothing at all.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.intelligence.public import EXECUTION_WINDOW
from tests.integration.conftest import WorkOrg, execute_approval
from tests.integration.test_execution_window import age_approval
from tests.integration.test_identity_resolution import CHANNEL, a_handle, mapping

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)

#: One promise, no request. The fake provider finds exactly one commitment span in this and no
#: work span, so a test that sees two proposals has found a real change in behaviour.
PROMISE = "Thanks for the call. I will send the revised quote on Friday."


def capture(
    api: TestClient, headers: dict[str, str], *, participants: list[dict], body: str = PROMISE
) -> dict:
    response = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": body,
            "source_system": CHANNEL,
            "source_ref": f"m-{uuid.uuid4().hex[:10]}",
            "participants": participants,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def analyze(api: TestClient, headers: dict[str, str], event: dict) -> dict:
    response = api.post(f"/api/v1/events/{event['id']}/analyze", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def a_known_speaker(
    api: TestClient, headers: dict[str, str], person_id: uuid.UUID
) -> list[dict]:
    """A participant whose handle a human has already vouched for."""
    handle = a_handle()
    mapping(api, headers, person_id, handle, confidence=95, confirm=True)
    return [{"role": "speaker", "external_handle": handle}]


def proposals_of(api: TestClient, headers: dict[str, str], result: dict) -> list[dict]:
    return [
        api.get(f"/api/v1/proposals/{proposal_id}", headers=headers).json()
        for proposal_id in result["proposal_ids"]
    ]


def approve(api: TestClient, headers: dict[str, str], proposal: dict) -> dict:
    record = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**headers, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert record.status_code == 201, record.text
    return record.json()


def tool_calls(api: TestClient, headers: dict[str, str], interaction_id: str) -> list[dict]:
    detail = api.get(f"/api/v1/ai-interactions/{interaction_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    return detail.json()["tool_calls"]


# --------------------------------------------------------------------------- the whole loop


def test_a_phone_number_becomes_an_approved_commitment(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """The vertical, in one test, starting from what a channel actually delivers."""
    event = capture(api, as_admin, participants=a_known_speaker(api, as_admin, work_org.member))
    result = analyze(api, as_admin, event)

    proposals = proposals_of(api, as_admin, result)
    assert len(proposals) == 1, "one promise, one proposal"
    proposal = proposals[0]
    assert proposal["target_type"] == "commitment", (
        "a resolved speaker is what makes this a commitment rather than a Work item"
    )
    assert proposal["action"]["tool"] == "create_commitment"
    assert proposal["action"]["arguments"]["committed_by_person_id"] == str(work_org.member)
    assert proposal["source_event_id"] == event["id"]

    # Nothing is written yet. Level 1 proposes; a person decides (PQ-3).
    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM commitment WHERE org_id = :org"), {"org": work_org.org_id}
    ).scalar_one() == 0

    record = approve(api, as_admin, proposal)
    outcome = execute_approval(api, as_admin, record["id"], worker_session_factory)

    assert outcome.execution_status == "executed", outcome.body
    assert outcome.entity_type == "commitment"

    created = api.get(f"/api/v1/commitments/{outcome.entity_id}", headers=as_admin).json()
    assert created["committed_by_person_id"] == str(work_org.member)
    assert created["origin_event_id"] == event["id"], (
        "the promise must name the message it was made in"
    )

    # The rest of the provenance is carried by the Proposal/Approval spine rather than by columns
    # on the Commitment, which is how this system was designed: the row points at its Event, the
    # approval points at the row and at the Proposal, the Proposal cites the Evidence, and the
    # Evidence names the interaction that produced it. Asserted link by link, because "auditable"
    # is only true if every hop resolves.
    assert outcome.body["proposal_id"] == proposal["id"]
    assert proposal["evidence_ids"] == result["evidence_ids"], "no provenance, no write (BR-C-03)"
    evidence = api.get(
        f"/api/v1/evidence/{result['evidence_ids'][0]}", headers=as_admin
    ).json()
    assert evidence["produced_by_type"] == "ai_interaction"
    assert evidence["produced_by_id"] == result["ai_interaction_id"]
    assert evidence["event_id"] == event["id"]


def test_the_evidence_quotes_the_message_verbatim(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """BR-E-05 on the commitment path. What a reviewer reads is the message, not a paraphrase."""
    event = capture(api, as_admin, participants=a_known_speaker(api, as_admin, work_org.member))
    result = analyze(api, as_admin, event)

    evidence = api.get(
        f"/api/v1/evidence/{result['evidence_ids'][0]}", headers=as_admin
    ).json()
    start, end = evidence["locator"]["char_start"], evidence["locator"]["char_end"]
    assert PROMISE[start:end] == evidence["excerpt"]
    assert evidence["target_type"] == "commitment"


# --------------------------------------------------------------------------- the refusals


def test_a_speaker_nobody_vouched_for_produces_nothing(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """BR-AI-34. An unresolved speaker is not a committer, and guessing one is the worst outcome.

    Note what does *not* happen: the observation is not quietly re-filed as Work. There is a person
    in this message and the system could not say who, which is a different fact from "somebody
    should do this" and is recorded as a refusal rather than converted into one.
    """
    event = capture(
        api, as_admin, participants=[{"role": "speaker", "external_handle": a_handle()}]
    )
    result = analyze(api, as_admin, event)

    assert result["proposal_ids"] == []
    assert result["evidence_ids"] == []
    refusals = [
        call for call in tool_calls(api, as_admin, result["ai_interaction_id"])
        if call["outcome"] == "refused"
    ]
    assert refusals, "a refusal is the authority model working and must be recorded"
    assert any("BR-AI-34" in (call["error"] or "") for call in refusals)


def test_no_speaker_at_all_degrades_to_work(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The conservative rule CP14 deliberately left alone, asserted so a change has to be meant.

    Nobody on this Event is recorded as having spoken, so there is no defensible committer — but
    the observation that something needs doing is still real, and Work needs no owner (BR-W-07,
    BR-W-15). The useful half survives; the unevidenced half is dropped (BR-AI-17).

    This is not "a commitment without a committer is a Work item". It is "what remains of an
    unattributable promise is a piece of work", which is a narrower claim and the only one the
    evidence supports.
    """
    event = capture(api, as_admin, participants=[])
    result = analyze(api, as_admin, event)

    proposals = proposals_of(api, as_admin, result)
    assert len(proposals) == 1
    assert proposals[0]["target_type"] == "work"
    assert proposals[0]["action"]["tool"] == "create_work"
    assert "committed_by_person_id" not in proposals[0]["action"]["arguments"]


# --------------------------------------------------------------------------- duplicates


def test_a_similar_work_item_does_not_suppress_a_commitment(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The CP14 defect, reproduced at the level it was found.

    A Work item worded like the promise used to make BR-AI-05 answer "this already exists" and the
    commitment was never proposed. A task somebody is tracking and a promise somebody made are not
    the same record, and one is not evidence about the other.
    """
    created = api.post(
        "/api/v1/work",
        json={"title": "I will send the revised quote on Friday"},
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text

    event = capture(api, as_admin, participants=a_known_speaker(api, as_admin, work_org.member))
    result = analyze(api, as_admin, event)

    proposals = proposals_of(api, as_admin, result)
    assert len(proposals) == 1, "the Work item suppressed the commitment (BR-AI-05 miscategorised)"
    assert proposals[0]["target_type"] == "commitment"

    searched = [
        call["tool_name"]
        for call in tool_calls(api, as_admin, result["ai_interaction_id"])
        if call["tool_name"].startswith("find_similar")
    ]
    assert searched == ["find_similar_commitments"], (
        "the audit row must say which corpus was searched"
    )


def test_the_same_promise_from_the_same_person_is_refused_as_a_duplicate(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-AI-05, asked the right question. Ten messages about one deadline are still one promise."""
    speaker = a_known_speaker(api, as_admin, work_org.member)
    first = analyze(api, as_admin, capture(api, as_admin, participants=speaker))
    record = approve(api, as_admin, proposals_of(api, as_admin, first)[0])
    outcome = execute_approval(api, as_admin, record["id"], worker_session_factory)
    assert outcome.execution_status == "executed", outcome.body

    second = analyze(api, as_admin, capture(api, as_admin, participants=speaker))

    assert second["proposal_ids"] == []
    refusals = [
        call for call in tool_calls(api, as_admin, second["ai_interaction_id"])
        if call["outcome"] == "refused"
    ]
    assert any("BR-AI-05" in (call["error"] or "") for call in refusals)
    assert not any("similar work" in (call["error"] or "") for call in refusals), (
        "a commitment refusal must be stated in the language of commitments"
    )


def test_the_same_promise_from_a_different_person_is_a_different_commitment(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """Two people promising the same deliverable are two promises.

    Collapsing them would erase one person's accountability, which is why the committer is part of
    the duplicate query rather than a filter applied to its results.
    """
    first = analyze(
        api,
        as_admin,
        capture(api, as_admin, participants=a_known_speaker(api, as_admin, work_org.member)),
    )
    record = approve(api, as_admin, proposals_of(api, as_admin, first)[0])
    assert execute_approval(
        api, as_admin, record["id"], worker_session_factory
    ).execution_status == "executed"

    second = analyze(
        api,
        as_admin,
        capture(api, as_admin, participants=a_known_speaker(api, as_admin, work_org.team_lead)),
    )

    proposals = proposals_of(api, as_admin, second)
    assert len(proposals) == 1
    assert proposals[0]["action"]["arguments"]["committed_by_person_id"] == str(
        work_org.team_lead
    )


# --------------------------------------------------------------------------- the old guarantees


def test_an_expired_approval_creates_no_commitment(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    owner_session: Session,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0051 on the newest path. A 24-hour-old approval authorises nothing."""
    result = analyze(
        api,
        as_admin,
        capture(api, as_admin, participants=a_known_speaker(api, as_admin, work_org.member)),
    )
    record = approve(api, as_admin, proposals_of(api, as_admin, result)[0])
    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=1),
    )

    outcome = execute_approval(api, as_admin, record["id"], worker_session_factory)

    assert outcome.execution_status != "executed"
    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM commitment WHERE org_id = :org"), {"org": work_org.org_id}
    ).scalar_one() == 0


def test_an_action_that_does_not_match_its_hash_creates_no_commitment(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0041 on the newest path. The approver agreed to a hash, and this is not it."""
    result = analyze(
        api,
        as_admin,
        capture(api, as_admin, participants=a_known_speaker(api, as_admin, work_org.member)),
    )
    proposal = proposals_of(api, as_admin, result)[0]
    forged = uuid.uuid4()

    scoped_session.rollback()
    scoped_session.execute(
        text(
            "INSERT INTO approval_record (id, org_id, proposal_id, approver_person_id, decision, "
            "approved_action, approved_action_hash, execution_status) VALUES "
            "(:id, :org, :proposal, :approver, 'approved', :action, "
            "'a-hash-of-something-else', 'pending')"
        ),
        {
            "id": forged,
            "org": work_org.org_id,
            "proposal": uuid.UUID(proposal["id"]),
            "approver": work_org.admin,
            "action": (
                '{"tool": "create_commitment", "tool_version": "v1", '
                '"arguments": {"statement": "Never approved"}}'
            ),
        },
    )
    scoped_session.commit()

    outcome = execute_approval(api, as_admin, forged, worker_session_factory)

    assert outcome.execution_status == "pending"
    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM commitment WHERE statement = 'Never approved'")
    ).scalar_one() == 0
