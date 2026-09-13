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


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/work",
        "/api/v1/commitments",
        "/api/v1/proposals",
        "/api/v1/events",
        "/api/v1/people",
    ],
)
def test_a_connector_reads_no_business_data(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    path: str,
) -> None:
    """Nothing reaches it, including the messages it delivered itself.

    The listing endpoints narrow by the grants a role holds rather than refusing up front, so a
    role with none of them gets an empty page rather than a 403. The confidentiality property is
    the same and is what is asserted here: a stolen delivery credential cannot be used to page
    through an organization, which is the thing it would otherwise be most useful for.

    The organization is populated first, so an empty answer means "narrowed to nothing" rather than
    "there was nothing".
    """
    assert (
        api.post(
            "/api/v1/work",
            json={"title": f"Visible to a member {uuid.uuid4().hex[:8]}"},
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 201
    )
    deliver(api, as_connector, parse_message(an_email()))

    response = api.get(path, headers=as_connector)
    assert response.status_code in (200, 403), response.text
    if response.status_code == 200:
        assert response.json()["items"] == [], f"GET {path} leaked rows to a connector"


def test_a_connector_cannot_read_back_the_event_it_delivered(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """Deliberate. The capture response already told it what happened."""
    event = deliver(api, as_connector, parse_message(an_email())).json()
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
