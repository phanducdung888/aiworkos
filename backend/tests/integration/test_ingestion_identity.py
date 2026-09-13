"""What an ingestion credential can and cannot do (CP20, ADR-0060, migration 0014).

ADR-0027 required an ingestion-only identity and nothing issued one, so a connector would have run
as `member` — 56 of the matrix's 83 cells, including `PROPOSAL.APPROVE`. A stolen delivery
credential could then have approved the AI's own proposals, closing the Level-2 loop with no person
anywhere in it.

Almost every test here is a refusal, and that is the shape of the thing: the role is defined by what
it cannot do.
"""

from __future__ import annotations

import uuid

import pytest
from connectors.imap.canonical import parse_message
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import (
    Realm,
    WorkOrg,
    auth,
    grant,
    subject_of,
)
from tests.integration.test_identity_resolution import a_handle, mapping
from tests.integration.test_imap_connector_journey import an_email, deliver

pytestmark = pytest.mark.integration


@pytest.fixture
def as_connector(
    realm: Realm, work_org: WorkOrg, scoped_session: Session
) -> dict[str, str]:
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "ingestion")
    scoped_session.commit()
    return auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)


# --------------------------------------------------------------------------- what it may do


def test_a_connector_may_deliver(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The control. Without it, a role that refused everything would pass this file."""
    response = deliver(api, as_connector, parse_message(an_email()))
    assert response.status_code == 201, response.text


def test_delivery_records_which_credential_delivered_it(
    api: TestClient,
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Audit is how "which connector delivered this" stays answerable six months later."""
    event = deliver(api, as_connector, parse_message(an_email())).json()

    scoped_session.rollback()
    entry = (
        scoped_session.execute(
            text(
                "SELECT actor, authorization_context FROM audit_entry "
                "WHERE resource_id = :id AND resource_type = 'event'"
            ),
            {"id": uuid.UUID(event["id"])},
        )
        .mappings()
        .one()
    )
    assert entry["actor"]["person_id"] == str(work_org.dept_lead)
    assert entry["authorization_context"]["role"] == "ingestion"


# --------------------------------------------------------------------------- what it may not


@pytest.mark.parametrize("resource", ["work", "commitment"])
def test_a_connector_may_write_nothing_but_an_event(
    api: TestClient,
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    resource: str,
) -> None:
    """One grant in the whole matrix, and every other write refused outright.

    The bodies are *valid* on purpose: schema validation runs before authorization, so a malformed
    request would be refused as 422 and would prove nothing about the role.
    """
    path, body = (
        ("/api/v1/work", {"title": "Delivered straight into the backlog"})
        if resource == "work"
        else (
            "/api/v1/commitments",
            {
                "statement": "A promise nobody made",
                "committed_by_person_id": str(work_org.member),
                "due_precision": "vague",
            },
        )
    )
    response = api.post(
        path, json=body, headers={**as_connector, "Idempotency-Key": str(uuid.uuid4())}
    )
    assert response.status_code == 403, f"POST {path} returned {response.status_code}"


#: Paths a connector must not read, and whether the denial is a refusal or an empty page.
#:
#: Both are denials. `work`, `people` and `events` narrow rows in the query, because the grants
#: there are genuinely narrower than the tenant for some role and a predicate is the only place
#: that can be expressed; the rest are refused outright, because no role reaches them at all
#: unless it reaches them organization-wide.
DENIED_TO_A_CONNECTOR = [
    ("/api/v1/work", "empty"),
    ("/api/v1/people", "empty"),
    ("/api/v1/commitments", "refused"),
    ("/api/v1/proposals", "refused"),
    ("/api/v1/events", "refused"),
    ("/api/v1/ai-interactions", "refused"),
    ("/api/v1/agent-policy", "refused"),
]


@pytest.mark.parametrize(("path", "shape"), DENIED_TO_A_CONNECTOR)
def test_a_connector_reads_no_business_data(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    path: str,
    shape: str,
) -> None:
    """Nothing reaches it through a listing, and the organization is populated first.

    This test existed before CP24 and passed while a connector could read every Proposal in the
    organization with its full action, every Commitment, every AI run and the autonomy policy. It
    asserted `items == []` against a fixture that had created one Work and nothing else — so for
    `/api/v1/proposals` and `/api/v1/commitments` it was asserting that an empty table is empty.

    An empty answer is only evidence of a control if something would otherwise have been in it, so
    every path here is also asked as an administrator and must come back non-empty.
    """
    populate(api, as_admin, as_connector, work_org)

    visible = api.get(path, headers=as_admin)
    assert visible.status_code == 200, visible.text
    assert visible.json()["items"], (
        f"GET {path} is empty for an administrator, so a connector seeing nothing proves nothing"
    )

    response = api.get(path, headers=as_connector)
    if shape == "refused":
        assert response.status_code == 403, f"GET {path} answered a connector"
    else:
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [], f"GET {path} leaked rows to a connector"


def populate(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    work_org: WorkOrg,
) -> dict[str, str]:
    """One row of everything the paths above list, so that an empty page means something."""
    assert (
        api.post(
            "/api/v1/work",
            json={"title": f"Visible to a member {uuid.uuid4().hex[:8]}"},
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 201
    )
    event = deliver(api, as_connector, parse_message(an_email())).json()
    commitment = api.post(
        "/api/v1/commitments",
        json={
            "statement": "Something a person promised",
            "committed_by_person_id": str(work_org.member),
            "due_precision": "exact",
            "due_date": "2020-01-01",
            "origin_event_id": event["id"],
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert commitment.status_code == 201, commitment.text
    evidence = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "commitment",
            "target_id": commitment.json()["id"],
            "assertion": "creates",
            "confidence": 100,
            "excerpt": parse_message(an_email()).body_text[:12],
            "text_locator": {"char_start": 0, "char_end": 12},
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert evidence.status_code == 201, evidence.text
    policy = api.put(
        "/api/v1/agent-policy",
        json={
            "capability": "extract",
            "entity_type": "commitment",
            "action": "create",
            "mode": "level_1_propose",
        },
        headers=as_admin,
    )
    assert policy.status_code == 200, policy.text
    proposal = api.post(
        "/api/v1/proposals",
        json={
            "kind": "create",
            "target_type": "commitment",
            "summary": "A promise somebody might have made",
            "tool": "create_commitment",
            "tool_version": "v1",
            "routed_to_person_id": str(work_org.admin),
            "source_event_id": event["id"],
            "evidence_ids": [evidence.json()["id"]],
            "confidence": 80,
            "arguments": {
                "statement": "A promise somebody might have made",
                "committed_by_person_id": str(work_org.member),
                "due_precision": "vague",
                "origin_event_id": event["id"],
                "evidence_ids": [evidence.json()["id"]],
            },
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert proposal.status_code == 201, proposal.text
    # An AIInteraction, through the agent path the deployment actually uses.
    run = api.post(f"/api/v1/events/{event['id']}/analyze", json={}, headers=as_admin)
    assert run.status_code == 201, run.text
    return {"event": event["id"], "commitment": commitment.json()["id"],
            "evidence": evidence.json()["id"], "proposal": proposal.json()["id"]}


def test_a_connector_reads_nothing_the_ai_path_produced(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
) -> None:
    """The same rule for a single row, with the rows actually present.

    The listing tests above are about collection endpoints. These are the detail reads the
    provenance walk uses, and each was open to a connector until CP24 for the same reason: the
    handler scoped the query to the organization and asked the matrix nothing.
    """
    rows = populate(api, as_admin, as_connector, work_org)
    for path in (
        f"/api/v1/commitments/{rows['commitment']}",
        f"/api/v1/evidence/{rows['evidence']}",
    ):
        assert api.get(path, headers=as_connector).status_code == 403, f"GET {path} was answered"
        assert api.get(path, headers=as_admin).status_code == 200, f"GET {path} is not a real path"


def test_listing_events_is_refused_to_a_connector(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
) -> None:
    """`(EVENT, LIST)` is a cell of its own, and the matrix denies it to `ingestion`.

    CP23 left this endpoint answering and relied on the reach predicate — derived from
    `(EVENT, READ)` — to make the page uninteresting. That reasoning was sound about *this* pair
    and unsound as a habit: it makes a published cell decorative, and it is the same reasoning that
    left Proposals, Commitments, AI runs and the autonomy policy fully readable by a connector
    until CP24 asked the running system rather than the code.

    The narrowing still exists and is still what governs what a connector can read one Event at a
    time — `test_a_connector_reads_back_only_the_events_it_delivered` is that test. This one is
    about the cell.
    """
    api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": "2026-09-12T09:00:00+00:00",
            "body_text": "written down by a person",
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    deliver(api, as_connector, parse_message(an_email()))

    listed = api.get("/api/v1/events", headers=as_connector)
    assert listed.status_code == 403, listed.text
    assert api.get("/api/v1/events", headers=as_admin).status_code == 200


def test_a_connector_reads_back_only_the_events_it_delivered(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
) -> None:
    """CP23's narrowing, both halves in one test.

    `PERSONAL` is rendered as `captured_by_person_id = :me` by the reach predicate, so the Event a
    connector delivered is readable and one somebody else captured is not — and "not" is a 404,
    which is the same answer as "does not exist", because whether an Event exists is itself
    tenant information.
    """
    mine = deliver(api, as_connector, parse_message(an_email())).json()
    theirs = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": "2026-09-12T09:00:00+00:00",
            "body_text": "written down by a person",
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    ).json()

    assert api.get(f"/api/v1/events/{mine['id']}", headers=as_connector).status_code == 200
    assert api.get(f"/api/v1/events/{theirs['id']}", headers=as_connector).status_code == 404


def test_a_connector_may_attach_to_its_own_event(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The reason CP23 opened anything at all (ADR-0063)."""
    event = deliver(api, as_connector, parse_message(an_email())).json()
    reserved = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "quote.pdf", "media_type": "application/pdf"},
        headers={**as_connector, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert reserved.status_code == 201, reserved.text
    assert reserved.json()["attachment"]["status"] == "pending"


def test_a_connector_may_not_attach_to_somebody_else_s_event(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
) -> None:
    """`ATTACH` is `PERSONAL` too, and the Event is loaded through the same narrowed read.

    So this is refused twice over: the connector cannot see the Event, and could not attach to it
    if it could.
    """
    theirs = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": "2026-09-12T09:00:00+00:00",
            "body_text": "not the connector's",
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    ).json()

    refused = api.post(
        f"/api/v1/events/{theirs['id']}/attachments",
        json={"filename": "intruder.pdf", "media_type": "application/pdf"},
        headers={**as_connector, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code in (403, 404)


def test_a_restricted_event_is_still_narrowed_for_a_connector(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    owner_session: Session,
) -> None:
    """BR-E-08 applies on top of the reach, not instead of it.

    A connector reads its own Events — and if one of those is later marked `restricted` and the
    connector is not a participant, the sensitivity predicate removes it anyway. Two predicates,
    both applied, which is what `readable_events` composes.
    """
    event = deliver(api, as_connector, parse_message(an_email())).json()
    assert api.get(f"/api/v1/events/{event['id']}", headers=as_connector).status_code == 200

    # As the table owner with the immutability trigger briefly off, because an Event's sensitivity
    # is frozen against every application path (BR-E-01). Reaching past a control no request can is
    # the only way to arrive at the state under test, and is itself a demonstration of the control.
    owner_session.rollback()
    owner_session.execute(
        text("SELECT set_config('app.current_org_id', :org, false)"),
        {"org": str(work_org.org_id)},
    )
    owner_session.execute(text("ALTER TABLE event DISABLE TRIGGER trg_event_immutable"))
    owner_session.execute(
        text("UPDATE event SET sensitivity = 'restricted' WHERE id = :id"),
        {"id": uuid.UUID(event["id"])},
    )
    owner_session.execute(text("ALTER TABLE event ENABLE TRIGGER trg_event_immutable"))
    owner_session.commit()
    owner_session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
    owner_session.commit()

    assert api.get(f"/api/v1/events/{event['id']}", headers=as_connector).status_code == 404


def test_a_connector_cannot_approve_the_proposals_its_messages_cause(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The refusal the whole role exists for.

    A connector that could approve would close the loop on itself: deliver a message, have the
    agent read it, approve what it proposed, and write into the organization with no person
    anywhere in the chain.
    """
    handle = a_handle()
    mapping(api, as_admin, work_org.member, handle, confidence=95, confirm=True)
    event = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": "2026-09-12T09:00:00+00:00",
            "body_text": "Thanks. I will send the revised quote by Friday.",
            "source_system": "openclaw.whatsapp",
            "source_ref": f"m-{uuid.uuid4().hex[:10]}",
            "participants": [{"role": "speaker", "external_handle": handle}],
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    ).json()
    analysis = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin).json()
    proposal = api.get(
        f"/api/v1/proposals/{analysis['proposal_ids'][0]}", headers=as_admin
    ).json()

    refused = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**as_connector, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert refused.status_code == 403


def test_a_connector_cannot_run_the_agent(
    api: TestClient,
    as_connector: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """Delivery is not analysis (ADR-0058 §5), enforced rather than left to the connector.

    404 rather than 403, and the reason is worth knowing: analysis reads the Event through the
    caller's own visibility (BR-E-08), and a connector cannot see Events at all. The refusal comes
    from the narrowing rather than from a separate check, which is the stronger arrangement — there
    is no permission to grant by accident later.
    """
    event = deliver(api, as_connector, parse_message(an_email()))
    assert event.status_code == 201
    assert api.post(
        f"/api/v1/events/{event.json()['id']}/analyze", headers=as_connector
    ).status_code in (403, 404)


def test_a_connector_cannot_change_the_agent_policy(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """It cannot widen what the agent may do with the messages it delivers (ADR-0047)."""
    refused = api.put(
        "/api/v1/agent-policy",
        json={
            "capability": "extract",
            "entity_type": "work",
            "action": "create",
            "mode": "level_2_approved_execution",
        },
        headers=as_connector,
    )
    assert refused.status_code == 403


def test_a_connector_cannot_vouch_for_an_identity(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-I-06 is the rule that turns an address into a person, and a connector is not part of it.

    If it could confirm a mapping, it could decide that a message it delivered was from whoever it
    chose — which is the attribution BR-AI-34 exists to prevent, arrived at from the other end.
    """
    refused = api.post(
        f"/api/v1/people/{work_org.member}/external-identities",
        json={
            "source_system": "email.imap",
            "external_id": "impostor@example.test",
            "confidence": 100,
        },
        headers={**as_connector, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 403


def test_a_connector_is_confined_to_its_own_organization(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The organization comes from the header and is checked against membership, never claimed."""
    elsewhere = {**as_connector, "X-Organization-Id": str(uuid.uuid4())}
    response = api.post(
        "/api/v1/events",
        json=parse_message(an_email()).as_event(),
        headers=elsewhere,
    )
    assert response.status_code == 404


def test_a_connector_cannot_assert_an_internal_event(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-E-13, and the loop it prevents.

    A connector reports what happened outside. One that could assert an internal Event could feed
    the system its own output and have it extracted again (BR-E-11).
    """
    refused = api.post(
        "/api/v1/events",
        json={
            "type": "SYSTEM_ACTIVITY",
            "occurred_at": "2026-09-12T09:00:00+00:00",
            "body_text": "Work status changed",
            "source_system": "email.imap",
            "source_ref": f"m-{uuid.uuid4().hex[:10]}",
        },
        headers={**as_connector, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 422
    assert refused.json()["rule"] == "BR-E-13"
