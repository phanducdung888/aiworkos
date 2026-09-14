"""The Work half of the loop, held to the same standard as the Commitment half (CP16).

The backend path already existed and CP14 proved the pieces. What this file proves is the product
claim: a message that asks for something becomes a Work item a person approved, with the chain back
to the words intact — and with the four things the AI is not allowed to decide still undecided.

Those four are the point of most of the file:

* it never names an owner (BR-W-15, ADR-0032 — `WorkAssignment` is the only way, and nothing on this
  path creates one);
* it never invents a Project (ADR-0029 — there are no synthetic containers, ever);
* it never sets a deadline on Work (CP15 removed `due_date` from the allow-list, because nothing
  populated it and a Work deadline is not a promise anybody made);
* it never writes anything before a person approves (PQ-3).
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.intelligence.linking import LinkCandidates
from app.platform.authz import Principal, Role
from tests.integration.conftest import WorkOrg, execute_approval

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)

def a_request() -> str:
    """A request, not a promise — and a different one every time.

    The fake provider reads "could you …" as work, so this produces exactly one Work proposal. The
    unique token matters: the suite shares a database, BR-AI-05 searches Work titles across it, and
    a fixed sentence would eventually be refused as a duplicate of a row another test left behind.
    That failure looks like a bug in the analysis and is a bug in the fixture.
    """
    return f"Hi team. Could you check whether the delivery date {uuid.uuid4().hex[:8]} still works?"


def capture(api: TestClient, headers: dict[str, str], body: str | None = None) -> dict:
    body = body if body is not None else a_request()
    response = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": body,
            "source_system": "openclaw.whatsapp",
            "source_ref": f"m-{uuid.uuid4().hex[:10]}",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def analyse(api: TestClient, headers: dict[str, str], event: dict) -> dict:
    response = api.post(f"/api/v1/events/{event['id']}/analyze", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def only_proposal(api: TestClient, headers: dict[str, str], result: dict) -> dict:
    assert len(result["proposal_ids"]) == 1, result
    return api.get(f"/api/v1/proposals/{result['proposal_ids'][0]}", headers=headers).json()


def approve_and_run(
    api: TestClient, headers: dict[str, str], proposal: dict, worker: sessionmaker[Session]
) -> dict:
    record = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**headers, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert record.status_code == 201, record.text
    outcome = execute_approval(api, headers, record.json()["id"], worker)
    assert outcome.execution_status == "executed", outcome.body
    assert outcome.entity_type == "work"
    return api.get(f"/api/v1/work/{outcome.entity_id}", headers=headers).json()


# --------------------------------------------------------------------------- the loop


def test_a_request_in_a_message_becomes_approved_work(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    event = capture(api, as_admin)
    proposal = only_proposal(api, as_admin, analyse(api, as_admin, event))

    assert proposal["target_type"] == "work"
    assert proposal["action"]["tool"] == "create_work"
    assert proposal["source_event_id"] == event["id"]

    # Nothing exists yet (PQ-3).
    scoped_session.rollback()
    before = scoped_session.execute(
        text("SELECT count(*) FROM work WHERE org_id = :org"), {"org": work_org.org_id}
    ).scalar_one()

    created = approve_and_run(api, as_admin, proposal, worker_session_factory)

    scoped_session.rollback()
    after = scoped_session.execute(
        text("SELECT count(*) FROM work WHERE org_id = :org"), {"org": work_org.org_id}
    ).scalar_one()
    assert after == before + 1
    assert created["title"] == proposal["action"]["arguments"]["title"]
    # BR-W-02: approved work starts in `todo`, not as a `proposed` row. The Proposal *was* the
    # proposed state; a second one would be two places recording the same pending-ness.
    assert created["status"] == "todo"


def test_approved_work_carries_no_owner_and_no_project(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0029, BR-W-07, BR-W-15. The three blanks the AI is not permitted to fill.

    A message saying "could somebody check this" evidences that something needs doing. It does not
    evidence who will do it or which project it belongs to, and filling either in would be the
    invention BR-AI-17 forbids — worse than a gap, because it looks like a decision somebody made.
    """
    proposal = only_proposal(api, as_admin, analyse(api, as_admin, capture(api, as_admin)))
    assert set(proposal["action"]["arguments"]) == {"title"}, (
        "the agent supplied something beyond a title"
    )

    created = approve_and_run(api, as_admin, proposal, worker_session_factory)

    assert created["project_id"] is None, "a synthetic project was invented"
    assert created["milestone_id"] is None
    assert created["due_date"] is None, "a deadline nobody set was attached to work"

    assignments = api.get(
        f"/api/v1/work/{created['id']}/assignments", headers=as_admin
    ).json()
    assert assignments["items"] == [], "the AI path created an assignment"


def test_the_agent_cannot_ask_for_a_project_it_was_not_offered(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The allow-list, not the prompt, is what makes this true (ADR-0052, ADR-0073).

    Until CP29 this asserted that `project_id` was not an argument an agent could supply at all.
    That is no longer the mechanism and the guarantee is unchanged: the key is permitted, and every
    *value* is checked against a candidate set derived deterministically from the evidence graph.
    An agent with no candidates — which is what this organization has — can still name nothing.

    The narrower assertions below are the ones CP15 and CP27 left behind, and they still hold: a
    milestone, a parent and a date are not things an agent may put in an intent at all, because
    nothing deterministic resolves them and the only way to fill one would be to guess.
    """
    from app.contexts.intelligence.intents import _ALLOWED_ARGUMENTS, _LINK_ARGUMENTS
    from app.platform.agentkit.contract import IntentKind

    permitted = _ALLOWED_ARGUMENTS[IntentKind.CREATE_WORK]
    assert "milestone_id" not in permitted
    assert "parent_work_id" not in permitted
    assert "due_date" not in permitted, "CP15 removed this; nothing populated it"
    assert permitted == frozenset({"title", "description", "project_id"})

    # And the half that replaced the absence: a permitted key is not a free field.
    assert "project_id" in _LINK_ARGUMENTS, (
        "project_id is accepted as an argument and unchecked as a value"
    )


# --------------------------------------------------------------------------- provenance


def test_approved_work_can_be_walked_back_to_the_message(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0056 is entity-agnostic, and this is the second entity proving it."""
    event = capture(api, as_admin)
    analysis = analyse(api, as_admin, event)
    created = approve_and_run(
        api, as_admin, only_proposal(api, as_admin, analysis), worker_session_factory
    )

    found = api.get(
        "/api/v1/proposals", params={"resulting_entity_id": created["id"]}, headers=as_admin
    ).json()
    assert [row["id"] for row in found["items"]] == analysis["proposal_ids"]

    proposal = api.get(
        f"/api/v1/proposals/{found['items'][0]['id']}", headers=as_admin
    ).json()
    approval = api.get(
        f"/api/v1/proposals/{proposal['id']}/approval", headers=as_admin
    ).json()
    assert approval["resulting_entity_type"] == "work"
    assert approval["resulting_entity_id"] == created["id"]

    evidence = api.get(
        f"/api/v1/evidence/{proposal['evidence_ids'][0]}", headers=as_admin
    ).json()
    assert evidence["excerpt"] in event["body_text"]
    assert evidence["produced_by_id"] == analysis["ai_interaction_id"]
    assert evidence["event_id"] == event["id"]


def test_work_a_person_typed_has_no_proposal_behind_it(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The other half of the same question. An empty chain is a fact, not a missing record."""
    created = api.post(
        "/api/v1/work",
        json={"title": f"Typed by hand {uuid.uuid4().hex[:8]}"},
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    ).json()

    found = api.get(
        "/api/v1/proposals", params={"resulting_entity_id": created["id"]}, headers=as_admin
    ).json()
    assert found["items"] == []


def test_the_source_column_says_a_person_made_it_and_that_is_deliberate(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """A finding worth pinning, because it reads like a bug and is not one.

    Work created from an approved Proposal carries `source = human`. `validate_work_creation`
    refuses `Source.AI` outright — AI-originated work exists as a Proposal until somebody approves
    it, and on approval the mutation is *the approver's act* (BR-AI-19), attributed to them.

    So `source` answers "was this typed, imported, or machine-generated without review", and the
    AI's involvement is answered by the chain in ADR-0056 instead. Recorded here so nobody
    "corrects" it into a second, contradictory record of provenance.
    """
    created = approve_and_run(
        api,
        as_admin,
        only_proposal(api, as_admin, analyse(api, as_admin, capture(api, as_admin))),
        worker_session_factory,
    )
    assert created["source"] == "human"

    found = api.get(
        "/api/v1/proposals", params={"resulting_entity_id": created["id"]}, headers=as_admin
    ).json()
    assert found["items"], "…and the chain is where the AI's involvement is actually recorded"


# ------------------------------------------------------- links from the evidence graph


def capture_in_thread(
    api: TestClient, headers: dict[str, str], thread: str, body: str | None = None
) -> dict:
    response = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": body if body is not None else a_request(),
            "source_system": "openclaw.whatsapp",
            "source_ref": f"m-{uuid.uuid4().hex[:10]}",
            "thread_ref": thread,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def resolve(
    factory: sessionmaker[Session], org: WorkOrg, event: dict
) -> LinkCandidates:
    """The candidate set, read the way the worker reads it.

    Scoped explicitly because RLS is keyed on `app.current_org_id` and a session that sets nothing
    sees nothing — which would make every assertion below pass for the wrong reason.
    """
    from app.contexts.intelligence.linking import candidates_for_event

    principal = Principal(
        person_id=org.admin, org_id=org.org_id, roles=frozenset({Role.ORG_ADMIN})
    )
    with factory() as session:
        session.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org.org_id)},
        )
        return candidates_for_event(session, principal, event_id=uuid.UUID(event["id"]))


def test_a_conversation_that_produced_work_becomes_a_candidate_for_its_next_message(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    app_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0073, end to end, on the path that matters.

    Message one is analysed, proposes work, a person approves it, and the Evidence written in that
    approved transaction attaches the message to the new Work item. Message two arrives in the same
    conversation — and *that* is what makes the first message's work a candidate for the second.

    Asserted at the resolver rather than through the model: what the model does with a candidate is
    the model's business and varies by provider, but whether the candidate exists at all is
    deterministic and is the half this system guarantees.
    """
    from app.contexts.signal.queries import SAME_THREAD

    thread = f"t-{uuid.uuid4().hex[:10]}"
    first = capture_in_thread(api, as_admin, thread)
    created = approve_and_run(
        api, as_admin, only_proposal(api, as_admin, analyse(api, as_admin, first)),
        worker_session_factory,
    )

    second = capture_in_thread(api, as_admin, thread)
    candidates = resolve(app_session_factory, work_org, second)

    assert [str(candidate.id) for candidate in candidates.work] == [created["id"]]
    assert candidates.work[0].reason == SAME_THREAD
    assert candidates.work[0].title == created["title"]


def test_a_different_conversation_is_not_a_candidate(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    app_session_factory: sessionmaker[Session],
) -> None:
    """The control, and the assertion that would fail if `thread_ref` were ignored.

    Without it the resolver would have to fall back on something — recency, similarity, the whole
    organization — and every one of those makes unrelated messages into candidates. This is the test
    that says it did not.
    """
    first = capture_in_thread(api, as_admin, f"t-{uuid.uuid4().hex[:10]}")
    approve_and_run(
        api, as_admin, only_proposal(api, as_admin, analyse(api, as_admin, first)),
        worker_session_factory,
    )

    elsewhere = capture_in_thread(api, as_admin, f"t-{uuid.uuid4().hex[:10]}")
    candidates = resolve(app_session_factory, work_org, elsewhere)

    assert candidates.is_empty, "a message from another conversation became a candidate"
