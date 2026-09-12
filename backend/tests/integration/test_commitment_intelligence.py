"""Deadlines, and the chain that explains where a promise came from (CP15).

Two things are being proved here and they are separate.

**A date is read, never invented (ADR-0055).** The model quotes the words that name a deadline;
WorkOS reads them against the Event's own `occurred_at` and the organization's calendar. The same
message analysed twice, a week apart, produces the same date — and a phrase nobody can place
produces no date at all rather than a plausible one.

**The provenance chain is walkable from the far end (ADR-0056).** Given a Commitment, a person must
be able to get back to the words somebody said. Every hop below uses an endpoint that already
exists; the only thing CP15 adds is the filter that makes the first hop possible.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.conftest import WorkOrg, execute_approval
from tests.integration.test_identity_resolution import CHANNEL, a_handle, mapping

pytestmark = pytest.mark.integration

#: Saturday 12 September 2026, 09:00 UTC. "by Friday" from here is the 18th.
SATURDAY = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)

BY_FRIDAY = "Thanks for the call. I will send the revised quote by Friday."
VAGUELY = "Thanks for the call. I will send the revised quote before the meeting."


def a_speaker(api: TestClient, headers: dict[str, str], person_id: uuid.UUID) -> list[dict]:
    handle = a_handle()
    mapping(api, headers, person_id, handle, confidence=95, confirm=True)
    return [{"role": "speaker", "external_handle": handle}]


def capture(
    api: TestClient,
    headers: dict[str, str],
    *,
    participants: list[dict],
    body: str = BY_FRIDAY,
    occurred_at: dt.datetime = SATURDAY,
) -> dict:
    response = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": occurred_at.isoformat(),
            "body_text": body,
            "source_system": CHANNEL,
            "source_ref": f"m-{uuid.uuid4().hex[:10]}",
            "participants": participants,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def analyse(api: TestClient, headers: dict[str, str], event: dict) -> dict:
    response = api.post(f"/api/v1/events/{event['id']}/analyze", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def proposal_of(api: TestClient, headers: dict[str, str], result: dict) -> dict:
    assert result["proposal_ids"], "nothing was proposed, so this proves nothing"
    return api.get(f"/api/v1/proposals/{result['proposal_ids'][0]}", headers=headers).json()


def approve_and_run(
    api: TestClient,
    headers: dict[str, str],
    proposal: dict,
    worker: sessionmaker[Session],
) -> dict:
    record = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**headers, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert record.status_code == 201, record.text
    outcome = execute_approval(api, headers, record.json()["id"], worker)
    assert outcome.execution_status == "executed", outcome.body
    return api.get(f"/api/v1/commitments/{outcome.entity_id}", headers=headers).json()


def set_timezone(session: Session, org_id: uuid.UUID, zone: str) -> None:
    session.rollback()
    session.execute(
        text("UPDATE organization SET timezone = :tz WHERE id = :org"),
        {"tz": zone, "org": org_id},
    )
    session.commit()


# --------------------------------------------------------------------------- deadlines


def test_a_quoted_deadline_becomes_a_dated_commitment(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    event = capture(api, as_admin, participants=a_speaker(api, as_admin, work_org.member))
    proposal = proposal_of(api, as_admin, analyse(api, as_admin, event))

    # The date is in the approved action, so the approver sees the deadline they are authorising
    # rather than discovering it afterwards.
    assert proposal["action"]["arguments"]["due_date"] == "2026-09-18"
    assert proposal["action"]["arguments"]["due_precision"] == "week"

    created = approve_and_run(api, as_admin, proposal, worker_session_factory)
    assert created["due_date"] == "2026-09-18"
    assert created["due_precision"] == "week"


def test_an_unplaceable_deadline_produces_a_promise_with_no_date(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-C-05. "Before the meeting" is a real deadline this system cannot place, and says so."""
    event = capture(
        api, as_admin, participants=a_speaker(api, as_admin, work_org.member), body=VAGUELY
    )
    created = approve_and_run(
        api, as_admin, proposal_of(api, as_admin, analyse(api, as_admin, event)),
        worker_session_factory,
    )
    assert created["due_date"] is None
    assert created["due_precision"] == "vague"


def test_the_date_follows_the_message_and_not_the_clock(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The same words, two different weeks, two different Fridays — and neither is "this week"."""
    speaker = a_speaker(api, as_admin, work_org.member)
    august = capture(
        api,
        as_admin,
        participants=speaker,
        occurred_at=dt.datetime(2026, 8, 1, 9, 0, tzinfo=dt.UTC),
    )
    september = capture(api, as_admin, participants=speaker)

    assert proposal_of(api, as_admin, analyse(api, as_admin, august))["action"]["arguments"][
        "due_date"
    ] == "2026-08-07"
    assert proposal_of(api, as_admin, analyse(api, as_admin, september))["action"]["arguments"][
        "due_date"
    ] == "2026-09-18"


def test_re_analysing_the_same_message_reads_the_same_date(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """Determinism where it matters: an old Event re-read must not acquire a newer deadline."""
    event = capture(api, as_admin, participants=a_speaker(api, as_admin, work_org.member))
    first = proposal_of(api, as_admin, analyse(api, as_admin, event))
    second = analyse(api, as_admin, event)

    # The second run finds the first commitment's proposal already standing, so it may raise
    # nothing — what must not happen is a *different* date appearing.
    for proposal_id in second["proposal_ids"]:
        again = api.get(f"/api/v1/proposals/{proposal_id}", headers=as_admin).json()
        assert again["action"]["arguments"].get("due_date") == first["action"]["arguments"][
            "due_date"
        ]


def test_the_organisation_calendar_decides_which_day_it_was(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """23:00 UTC on a Friday is already Saturday in Hanoi, so "by Friday" is a week later."""
    late_friday = dt.datetime(2026, 9, 11, 23, 0, tzinfo=dt.UTC)
    speaker = a_speaker(api, as_admin, work_org.member)

    utc_event = capture(api, as_admin, participants=speaker, occurred_at=late_friday)
    in_utc = proposal_of(api, as_admin, analyse(api, as_admin, utc_event))
    assert in_utc["action"]["arguments"]["due_date"] == "2026-09-11"

    set_timezone(scoped_session, work_org.org_id, "Asia/Ho_Chi_Minh")
    hanoi_event = capture(api, as_admin, participants=speaker, occurred_at=late_friday)
    in_hanoi = proposal_of(api, as_admin, analyse(api, as_admin, hanoi_event))
    assert in_hanoi["action"]["arguments"]["due_date"] == "2026-09-18"
    set_timezone(scoped_session, work_org.org_id, "UTC")


def test_a_dated_commitment_reaches_the_overdue_question(
    api: TestClient,
    as_admin: dict[str, str],
    as_member: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-C-06 only sees promises with dates, which is why reading the deadline is worth doing.

    A vague promise is deliberately invisible here — it ages into a review prompt rather than into
    a missed-deadline alert about a deadline nobody set.
    """
    event = capture(api, as_admin, participants=a_speaker(api, as_admin, work_org.member))
    dated = approve_and_run(
        api, as_admin, proposal_of(api, as_admin, analyse(api, as_admin, event)),
        worker_session_factory,
    )
    # Open it and let its deadline pass. `captured` is not yet a promise anybody is answering for.
    #
    # As the *committer*, not as the admin who approved the proposal: BR-C-09 gives the transition
    # to the people the promise is about, and approving a Proposal is not one of the ways in.
    opened = api.post(
        f"/api/v1/commitments/{dated['id']}/status",
        json={"target": "open"},
        headers={**as_member, "If-Match": f'W/"{dated["version"]}"'},
    )
    assert opened.status_code == 200, opened.text
    scoped_session.rollback()
    scoped_session.execute(
        text("UPDATE commitment SET due_date = :past WHERE id = :id"),
        {"past": dt.date(2020, 1, 1), "id": uuid.UUID(dated["id"])},
    )
    scoped_session.commit()

    overdue = api.get("/api/v1/commitments/overdue/today", headers=as_admin).json()
    assert dated["id"] in [row["id"] for row in overdue["items"]]


# --------------------------------------------------------------------------- provenance


def test_a_commitment_can_be_walked_back_to_the_words_somebody_said(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0056, hop by hop, through endpoints that already existed plus one new filter.

    This is the question the product has to be able to answer — "why does WorkOS believe this?" —
    and it is answered by walking, not by denormalising the answer onto the Commitment.
    """
    event = capture(api, as_admin, participants=a_speaker(api, as_admin, work_org.member))
    analysis = analyse(api, as_admin, event)
    created = approve_and_run(
        api, as_admin, proposal_of(api, as_admin, analysis), worker_session_factory
    )

    # 1. Commitment -> Proposal, the hop that did not exist before CP15.
    found = api.get(
        "/api/v1/proposals", params={"resulting_entity_id": created["id"]}, headers=as_admin
    ).json()
    assert [row["id"] for row in found["items"]] == analysis["proposal_ids"]
    # The list resource is a reference; the citations live on the detail.
    proposal = api.get(
        f"/api/v1/proposals/{found['items'][0]['id']}", headers=as_admin
    ).json()

    # 2. Proposal -> ApprovalRecord: who authorised it, and exactly what they authorised.
    approval = api.get(
        f"/api/v1/proposals/{proposal['id']}/approval", headers=as_admin
    ).json()
    assert approval["approver_person_id"] == str(work_org.admin)
    assert approval["resulting_entity_id"] == created["id"]
    assert approval["approved_action_hash"] == proposal["action_hash"]

    # 3. Proposal -> Evidence: the verbatim words.
    evidence = api.get(
        f"/api/v1/evidence/{proposal['evidence_ids'][0]}", headers=as_admin
    ).json()
    assert evidence["excerpt"] in event["body_text"]

    # 4. Evidence -> AIInteraction and Evidence -> Event: which run said it, reading what.
    assert evidence["produced_by_type"] == "ai_interaction"
    assert evidence["produced_by_id"] == analysis["ai_interaction_id"]
    assert evidence["event_id"] == event["id"]
    assert created["origin_event_id"] == event["id"]

    interaction = api.get(
        f"/api/v1/ai-interactions/{analysis['ai_interaction_id']}", headers=as_admin
    ).json()
    assert interaction["tool_calls"], "the run must be able to account for what it did"


def test_the_provenance_filter_cannot_reach_another_organisation(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    other_org_person: uuid.UUID,
) -> None:
    """A new filter is a new way to ask a question, and it is scoped like every other one."""
    found = api.get(
        "/api/v1/proposals",
        params={"resulting_entity_id": str(other_org_person)},
        headers=as_admin,
    )
    assert found.status_code == 200
    assert found.json()["items"] == []
