"""The chain, end to end: Event → Evidence → Proposal → ApprovalRecord → mutation → audit.

BR-PR-08 requires that chain to be traversable in both directions for every proposed change. Most of
this file walks it forwards; the last section walks it backwards, from a created Work item to the
words somebody actually said, because a provenance link that only resolves one way is half a link
and the half that is missing is the one an auditor asks for.

The security claims are tested where they would break rather than where they are stated. Approving
someone else's proposal, executing a hash that no longer matches, executing twice, executing an
action a reviewer never saw — each is a request this file actually makes.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.conftest import Realm, WorkOrg, auth, execute_approval, grant, subject_of

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
SAID = "Nam will send the revised quote by Friday."


def an_event(api: TestClient, headers: dict[str, str], body: str = SAID) -> dict:
    response = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": body,
            "source_system": "openclaw.whatsapp",
            "source_ref": f"msg-{uuid.uuid4().hex[:10]}",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def some_evidence(
    api: TestClient, headers: dict[str, str], event: dict, *, quote: str = "revised quote"
) -> dict:
    start = event["body_text"].index(quote)
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "creates",
            "text_locator": {"char_start": start, "char_end": start + len(quote)},
            "excerpt": quote,
            "confidence": 85,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def a_proposal(
    api: TestClient,
    headers: dict[str, str],
    *,
    routed_to: uuid.UUID,
    event: dict | None = None,
    evidence: dict | None = None,
    **over: object,
) -> dict:
    payload: dict[str, object] = {
        "kind": "create",
        "target_type": "work",
        "summary": "Create work: send the revised quote",
        "tool": "create_work",
        "tool_version": "v1",
        "arguments": {"title": "Send the revised quote"},
        "routed_to_person_id": str(routed_to),
        "confidence": 85,
    }
    if event is not None:
        payload["source_event_id"] = event["id"]
    if evidence is not None:
        payload["evidence_ids"] = [evidence["id"]]
    payload.update(over)
    response = api.post("/api/v1/proposals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def etag(response) -> str:
    tag = response.headers.get("ETag")
    assert tag, "a versioned resource must return an ETag"
    return tag


def decide(
    api: TestClient, headers: dict[str, str], proposal: dict, **body: object
) -> object:
    payload: dict[str, object] = {"decision": "approved"}
    payload.update(body)
    return api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json=payload,
        headers={**headers, "If-Match": f'W/"{proposal["version"]}"'},
    )


# --------------------------------------------------------------------------- the whole chain


def test_the_chain_runs_from_a_message_to_a_work_item(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    event = an_event(api, as_admin)
    evidence = some_evidence(api, as_admin, event)
    proposal = a_proposal(
        api, as_admin, routed_to=work_org.admin, event=event, evidence=evidence
    )
    assert proposal["status"] == "pending"
    assert proposal["evidence_ids"] == [evidence["id"]]

    approval = decide(api, as_admin, proposal)
    assert approval.status_code == 201, approval.text
    record = approval.json()
    assert record["decision"] == "approved"
    assert record["execution_status"] == "pending", "approving is not executing (BR-PR-01)"

    executed = execute_approval(api, as_admin, record['id'], worker_session_factory)
    assert executed.execution_status == "executed", executed.body
    assert executed.entity_type == "work"

    created = api.get(f"/api/v1/work/{executed.entity_id}", headers=as_admin)
    assert created.status_code == 200
    assert created.json()["title"] == "Send the revised quote"

    # BR-AI-19: the mutation is attributed to the approver and carries the chain that authorised it.
    scoped_session.rollback()
    entry = scoped_session.execute(
        text(
            "SELECT actor FROM audit_entry WHERE resource_id = :id AND action = 'create'"
        ),
        {"id": uuid.UUID(executed.entity_id or "")},
    ).mappings().one()
    assert entry["actor"]["person_id"] == str(work_org.admin)
    assert entry["actor"]["extra"]["executed_via"] == "ai_tool"
    assert entry["actor"]["extra"]["proposal_id"] == proposal["id"]
    assert entry["actor"]["extra"]["approval_record_id"] == record["id"]


def test_the_chain_traverses_backwards(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-PR-08. From the created Work item back to the words somebody actually said."""
    event = an_event(api, as_admin)
    evidence = some_evidence(api, as_admin, event)
    proposal = a_proposal(
        api, as_admin, routed_to=work_org.admin, event=event, evidence=evidence
    )
    record = decide(api, as_admin, proposal).json()
    outcome = execute_approval(api, as_admin, record['id'], worker_session_factory)

    scoped_session.rollback()
    # work <- approval_record <- proposal <- proposal_evidence <- evidence <- event
    row = scoped_session.execute(
        text(
            """
            SELECT e.body_text, v.excerpt, p.summary
            FROM approval_record a
            JOIN proposal p ON p.id = a.proposal_id AND p.org_id = a.org_id
            JOIN proposal_evidence pe ON pe.proposal_id = p.id AND pe.org_id = p.org_id
            JOIN evidence v ON v.id = pe.evidence_id AND v.org_id = pe.org_id
            JOIN event e ON e.id = v.event_id AND e.org_id = v.org_id
            WHERE a.resulting_entity_id = :work AND a.org_id = :org
            """
        ),
        {"work": uuid.UUID(outcome.entity_id or ""), "org": work_org.org_id},
    ).mappings().one()
    assert row["excerpt"] in row["body_text"]
    assert row["body_text"] == SAID


# --------------------------------------------------------------------------- approval binding


def test_the_approved_action_cannot_be_tampered_with(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """The first line of defence: an ApprovalRecord is immutable at the database.

    Editing the approved action is refused even in raw SQL as the owner, so the attack this design
    worries about — change what was approved between approval and execution — cannot be carried out
    by anything with a database connection, let alone through the API.
    """
    proposal = a_proposal(api, as_admin, routed_to=work_org.admin)
    record = decide(api, as_admin, proposal).json()

    scoped_session.rollback()
    with pytest.raises(Exception, match="immutable"):
        scoped_session.execute(
            text(
                "UPDATE approval_record SET approved_action = "
                "jsonb_set(approved_action, '{arguments,title}', '\"Something else\"') "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(record["id"])},
        )
    scoped_session.rollback()


def test_execution_refuses_a_record_whose_hash_does_not_match_its_action(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """The second line: BR-AI-18's recomputation, tested independently of the trigger.

    The row is inserted already inconsistent, which is what a bug in some future writer would
    produce. Execution must refuse rather than trust either half — not the action, because the
    human approved the hash of something else, and not the hash, because it names nothing runnable.
    """
    proposal = a_proposal(api, as_admin, routed_to=work_org.admin)
    forged = uuid.uuid4()

    scoped_session.rollback()
    scoped_session.execute(
        text(
            "INSERT INTO approval_record (id, org_id, proposal_id, approver_person_id, decision, "
            "approved_action, approved_action_hash, execution_status) VALUES "
            "(:id, :org, :proposal, :approver, 'approved', "
            "'{\"tool\": \"create_work\", \"tool_version\": \"v1\", "
            "\"arguments\": {\"title\": \"Never approved\"}}'::jsonb, "
            "'a-hash-of-something-else', 'pending')"
        ),
        {
            "id": forged,
            "org": work_org.org_id,
            "proposal": uuid.UUID(proposal["id"]),
            "approver": work_org.admin,
        },
    )
    scoped_session.commit()

    refused = execute_approval(api, as_admin, forged, worker_session_factory)
    assert refused.execution_status == "pending", (
        "a refused execution must leave the approval usable"
    )
    assert refused.entity_id is None

    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM work WHERE title = 'Never approved'")
    ).scalar_one() == 0


def test_an_approval_executes_exactly_once(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    proposal = a_proposal(api, as_admin, routed_to=work_org.admin)
    record = decide(api, as_admin, proposal).json()

    first = execute_approval(api, as_admin, record['id'], worker_session_factory)
    second = execute_approval(api, as_admin, record['id'], worker_session_factory)
    assert first.execution_status == "executed"
    # Queueing again is accepted and does nothing: the approval is spent, so the worker
    # completes the job without repeating the mutation (ADR-0044).
    assert second.execution_status == "executed"
    assert second.entity_id == first.entity_id

    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM work WHERE title = 'Send the revised quote'")
    ).scalar_one() == 1


def test_a_rejection_authorises_nothing(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    proposal = a_proposal(api, as_admin, routed_to=work_org.admin)
    record = decide(
        api, as_admin, proposal, decision="rejected", rejection_reason="not ours to do"
    ).json()
    assert record["execution_status"] == "not_applicable"

    refused = execute_approval(api, as_admin, record['id'], worker_session_factory)
    assert refused.execution_status == "not_applicable"
    assert refused.entity_id is None

    read = api.get(f"/api/v1/proposals/{proposal['id']}", headers=as_admin).json()
    assert read["status"] == "rejected"
    assert read["rejection_reason"] == "not ours to do"


def test_a_revised_proposal_requires_a_new_approval(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0041, the property the whole design rests on.

    Revising produces a new action, a new hash and a new Proposal. The original is superseded, and
    nothing approved for it can reach the revision.
    """
    original = a_proposal(api, as_admin, routed_to=work_org.admin)
    revised = api.post(
        f"/api/v1/proposals/{original['id']}/revise",
        json={"arguments": {"title": "Send the revised quote to finance"}},
        headers={**as_admin, "If-Match": f'W/"{original["version"]}"'},
    )
    assert revised.status_code == 201, revised.text
    replacement = revised.json()
    assert replacement["id"] != original["id"]
    assert replacement["supersedes_proposal_id"] == original["id"]
    assert replacement["action_hash"] != original["action_hash"]

    superseded = api.get(f"/api/v1/proposals/{original['id']}", headers=as_admin).json()
    assert superseded["status"] == "superseded"
    # The original can no longer be decided, so no approval for it can ever exist.
    assert decide(api, as_admin, superseded).status_code == 422


def test_approving_with_edits_approves_the_edited_action(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-PR-06. The hash is of what the approver agreed to, never of what was proposed."""
    proposal = a_proposal(api, as_admin, routed_to=work_org.admin)
    record = decide(
        api,
        as_admin,
        proposal,
        decision="approved_with_edits",
        edited_arguments={"title": "Send the quote, with the discount applied"},
    ).json()
    assert record["decision"] == "approved_with_edits"
    assert record["approved_action_hash"] != proposal["action_hash"]
    assert record["edits"]["to"]["title"] == "Send the quote, with the discount applied"

    outcome = execute_approval(api, as_admin, record['id'], worker_session_factory)
    created = api.get(f"/api/v1/work/{outcome.entity_id}", headers=as_admin).json()
    assert created["title"] == "Send the quote, with the discount applied"


def test_edits_are_refused_on_a_plain_approval(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    proposal = a_proposal(api, as_admin, routed_to=work_org.admin)
    response = decide(
        api, as_admin, proposal, decision="approved", edited_arguments={"title": "sneaky"}
    )
    assert response.status_code == 422
    assert "BR-PR-06" in response.text


# --------------------------------------------------------------------------- authorization


def test_a_proposal_routed_elsewhere_cannot_be_approved(
    api: TestClient, realm: Realm, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-PR-04. A proposal anyone can approve is a queue somebody empties, not a review."""
    proposal = a_proposal(api, as_admin, routed_to=work_org.team_lead)
    member = auth(realm, subject_of(scoped_session, work_org.member), work_org.org_id)
    response = decide(api, member, proposal)
    assert response.status_code in (403, 422)

    still_pending = api.get(f"/api/v1/proposals/{proposal['id']}", headers=as_admin).json()
    assert still_pending["status"] == "pending"


def test_approving_grants_the_approver_no_new_permission(
    api: TestClient, realm: Realm, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-PR-05, the claim that makes approval safe to delegate.

    The Tool Gateway executes as the approver and the application service authorizes them normally.
    A viewer who somehow holds a proposal cannot obtain, through approving it, a power they do not
    have — the refusal comes from the service the tool calls, not from the approval path.
    """
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "viewer")
    viewer = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    proposal = a_proposal(api, as_admin, routed_to=work_org.dept_lead)

    response = decide(api, viewer, proposal)
    # Either the viewer cannot decide at all, or they can decide and the execution is refused.
    if response.status_code == 201:
        executed = execute_approval(api, viewer, response.json()['id'], worker_session_factory)
        assert executed.execution_status == "pending", (
            "a viewer must not create work by approving; the service refuses at execution"
        )
        assert executed.entity_id is None
    else:
        assert response.status_code == 403


def test_a_proposal_naming_an_unregistered_tool_is_refused_at_creation(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """ADR-0042. A proposal that could never execute must not reach a reviewer."""
    response = api.post(
        "/api/v1/proposals",
        json={
            "kind": "create",
            "target_type": "work",
            "summary": "Delete everything",
            "tool": "delete_work",
            "tool_version": "v1",
            "arguments": {},
            "routed_to_person_id": str(work_org.admin),
        },
        headers=as_admin,
    )
    assert response.status_code == 422
    assert "BR-AI-16" in response.text


def test_a_proposal_targeting_a_person_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-AI-08, BR-AI-23. No tool changes Identity, so this could only ever expire."""
    response = api.post(
        "/api/v1/proposals",
        json={
            "kind": "update",
            "target_type": "person",
            "target_id": str(work_org.member),
            "summary": "Make them an admin",
            "tool": "create_work",
            "tool_version": "v1",
            "arguments": {"title": "x"},
            "routed_to_person_id": str(work_org.admin),
        },
        headers=as_admin,
    )
    assert response.status_code == 422
    assert "BR-AI-08" in response.text


# --------------------------------------------------------------------------- tenancy


def test_another_organizations_proposal_is_invisible(
    api: TestClient, as_admin: dict[str, str], roles: None, app_session_factory,
    worker_session_factory: sessionmaker[Session],
) -> None:
    from app.platform.ids import uuid7

    other_org, other_person, other_proposal = uuid7(), uuid7(), uuid7()
    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(other_org)}
    )
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Rival', :slug)"),
        {"id": other_org, "slug": f"rival-{uuid.uuid4().hex[:12]}"},
    )
    session.execute(
        text(
            "INSERT INTO person (id, org_id, display_name, status) "
            "VALUES (:id, :org, 'Rival', 'active')"
        ),
        {"id": other_person, "org": other_org},
    )
    session.execute(
        text(
            "INSERT INTO proposal (id, org_id, kind, target_type, summary, action, action_hash, "
            "routed_to_person_id, expires_at) VALUES (:id, :org, 'create', 'work', 'theirs', "
            "'{}'::jsonb, 'h', :person, now() + interval '7 days')"
        ),
        {"id": other_proposal, "org": other_org, "person": other_person},
    )
    session.commit()
    session.close()

    assert api.get(f"/api/v1/proposals/{other_proposal}", headers=as_admin).status_code == 404
    listed = api.get("/api/v1/proposals", headers=as_admin).json()["items"]
    assert all(row["id"] != str(other_proposal) for row in listed)
