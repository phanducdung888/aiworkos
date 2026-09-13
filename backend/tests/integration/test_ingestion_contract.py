"""The contract a channel connector has to keep, executed rather than described (ADR-0058).

CP19B deliberately adds no connector port and no adapter. The seam is the capture API a connector
already has, so the useful artefact is not an interface — it is this file: a conformance suite that
says exactly what a connector must send, and proves the platform does the rest.

The order below is the pipeline:

    external message → Event → participants → identity resolution → AI analysis

A connector is responsible for the first arrow and nothing else. Every test after the second one is
about something the connector must *not* do, and must not be able to do.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.integration.conftest import WorkOrg
from tests.integration.test_identity_resolution import a_handle, mapping

pytestmark = pytest.mark.integration

#: What a connector calls itself. Stable, because BR-E-02 is keyed on it.
CHANNEL = "openclaw.whatsapp"
NOW = dt.datetime(2026, 9, 13, 9, 0, tzinfo=dt.UTC)
MESSAGE = "Thanks for the call. I will send the revised quote by Friday."


def vendor_message_id() -> str:
    """The identifier the vendor gave this message. A connector never invents one."""
    return f"wamid.{uuid.uuid4().hex}"


def idempotency_key(source_system: str, source_ref: str, body: str) -> str:
    """ADR-0058 §3: derived, not random — and derived from the content as well as the reference.

    A random key per attempt would make every redelivery a new action, which is the failure the
    header exists to prevent. Deriving it from `(source_system, source_ref)` alone fails the other
    way: an *edited* message legitimately carries the same reference, and the guard would refuse it
    as the same key with a different body. It is a revision (BR-E-02), not a retry.

    So the key is over the reference and the content together. The same bytes always produce the
    same key from any connector instance, on any host, after any restart; different bytes under the
    same reference are a different action, and the platform decides what kind.
    """
    material = f"{source_system}\x00{source_ref}\x00{body}".encode()
    return str(uuid.UUID(hashlib.sha256(material).hexdigest()[:32]))


def deliver(
    api: TestClient,
    headers: dict[str, str],
    *,
    source_ref: str,
    body: str = MESSAGE,
    participants: list[dict] | None = None,
    source_system: str = CHANNEL,
    occurred_at: dt.datetime = NOW,
):
    """One delivery, exactly as a conforming connector makes it."""
    return api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": occurred_at.isoformat(),
            "body_text": body,
            "source_system": source_system,
            "source_ref": source_ref,
            "participants": participants if participants is not None else [],
        },
        headers={
            **headers,
            "Idempotency-Key": idempotency_key(source_system, source_ref, body),
        },
    )


def event_count(session: Session, org_id: uuid.UUID) -> int:
    session.rollback()
    return session.execute(
        text("SELECT count(*) FROM event WHERE org_id = :org"), {"org": org_id}
    ).scalar_one()


# --------------------------------------------------------------------------- delivery


def test_a_delivered_message_becomes_an_event(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    response = deliver(api, as_admin, source_ref=vendor_message_id())
    assert response.status_code == 201, response.text
    event = response.json()
    assert event["origin"] == "external"
    assert event["source_system"] == CHANNEL
    assert event["body_text"] == MESSAGE


def test_redelivering_the_same_message_creates_one_event(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The reason the key is derived. Every transport redelivers an unacknowledged message.

    Both the idempotency guard and BR-E-02 would answer this correctly on their own. The point is
    that a conforming connector does not have to know which one caught it.
    """
    reference = vendor_message_id()
    before = event_count(scoped_session, work_org.org_id)

    first = deliver(api, as_admin, source_ref=reference)
    second = deliver(api, as_admin, source_ref=reference)
    third = deliver(api, as_admin, source_ref=reference)

    assert first.status_code == 201
    assert first.json()["id"] == second.json()["id"] == third.json()["id"]
    assert event_count(scoped_session, work_org.org_id) == before + 1


def test_an_edited_message_under_the_same_reference_is_a_revision(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-E-02. Same reference, different content: a correction, not a duplicate and not a new fact.

    Note what the connector does *not* do — it does not decide this. It sends the same reference
    with the new text and the platform classifies it.
    """
    reference = vendor_message_id()
    original = deliver(api, as_admin, source_ref=reference).json()
    edited = deliver(
        api, as_admin, source_ref=reference, body=f"{MESSAGE} Actually, make it Monday."
    )

    assert edited.status_code == 201, edited.text
    assert edited.json()["id"] != original["id"]
    assert edited.json()["revision_of_event_id"] == original["id"]


def test_the_same_reference_from_another_channel_is_another_message(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """`source_system` is half the key, so two connectors cannot collide on a shared id space."""
    reference = vendor_message_id()
    first = deliver(api, as_admin, source_ref=reference)
    other = deliver(api, as_admin, source_ref=reference, source_system="acme.sms")

    assert other.status_code == 201
    assert other.json()["id"] != first.json()["id"]


def test_one_organizations_delivery_is_invisible_to_another(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    other_org_person: uuid.UUID,
    scoped_session: Session,
) -> None:
    """Tenancy on the newest path in. The connector supplies no org; the credential decides it."""
    reference = vendor_message_id()
    deliver(api, as_admin, source_ref=reference)

    scoped_session.rollback()
    elsewhere = scoped_session.execute(
        text("SELECT count(*) FROM event WHERE source_ref = :ref AND org_id <> :org"),
        {"ref": reference, "org": work_org.org_id},
    ).scalar_one()
    assert elsewhere == 0


# --------------------------------------------------------------------------- participants


def test_a_connector_sends_handles_and_the_platform_resolves_them(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """ADR-0058 §2 meeting ADR-0054.

    The connector knows a phone number and nothing else — it has no way to know a `person_id` and
    no authority to assert one. Resolution is the platform's, and it happens at capture.
    """
    known = a_handle()
    mapping(api, as_admin, work_org.member, known, confidence=95, confirm=True)
    unknown = a_handle()

    event = deliver(
        api,
        as_admin,
        source_ref=vendor_message_id(),
        participants=[
            {"role": "speaker", "external_handle": known},
            {"role": "recipient", "external_handle": unknown},
        ],
    ).json()

    detail = api.get(f"/api/v1/events/{event['id']}", headers=as_admin).json()
    by_handle = {row["external_handle"]: row for row in detail["participants"]}
    assert by_handle[known]["person_id"] == str(work_org.member)
    assert by_handle[unknown]["person_id"] is None
    assert by_handle[unknown]["external_handle"] == unknown


def test_a_connector_cannot_declare_who_somebody_is(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The rule that makes §2 enforceable rather than advisory.

    A `person_id` in the payload is checked against this organization before it is stored, so a
    connector cannot name somebody it has no business naming — and a connector authenticated as an
    ingestion-only identity (ADR-0027) is refused the whole call long before this.
    """
    response = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": MESSAGE,
            "source_system": CHANNEL,
            "source_ref": vendor_message_id(),
            "participants": [{"role": "speaker", "person_id": str(uuid.uuid4())}],
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert response.json()["rule"] == "BR-G-01"


def test_delivery_is_not_analysis(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """ADR-0058 §5. A connector delivers and stops.

    Capture must not spend a model call, and must not put "the AI ran" on a path nobody chose
    (BR-AI-01). Asserted by counting interactions rather than by reading the router.
    """

    def interactions() -> int:
        scoped_session.rollback()
        return scoped_session.execute(
            text("SELECT count(*) FROM ai_interaction WHERE org_id = :org"),
            {"org": work_org.org_id},
        ).scalar_one()

    before = interactions()
    deliver(api, as_admin, source_ref=vendor_message_id())
    assert interactions() == before


# --------------------------------------------------------------------------- the rest of the pipe


def test_a_delivered_message_runs_the_whole_pipeline_when_asked(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The claim ADR-0058 rests on: nothing new is needed behind the connector.

    Delivery, resolution, analysis, citation and proposal, with the connector contributing only the
    first step.
    """
    handle = a_handle()
    mapping(api, as_admin, work_org.member, handle, confidence=95, confirm=True)
    event = deliver(
        api,
        as_admin,
        source_ref=vendor_message_id(),
        participants=[{"role": "speaker", "external_handle": handle}],
    ).json()

    analysis = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin)
    assert analysis.status_code == 201, analysis.text
    result = analysis.json()
    assert result["proposal_ids"], "the pipeline produced nothing from a delivered message"

    proposal = api.get(
        f"/api/v1/proposals/{result['proposal_ids'][0]}", headers=as_admin
    ).json()
    assert proposal["target_type"] == "commitment"
    assert proposal["action"]["arguments"]["committed_by_person_id"] == str(work_org.member)
    assert proposal["action"]["arguments"]["due_date"] == "2026-09-18"
    assert proposal["status"] == "pending", "delivery must not have executed anything"


def test_an_internally_generated_event_is_not_extractable(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-E-11, and the loop it prevents.

    A connector may only assert that something happened outside (BR-E-13). The capture API refuses
    an internal-origin claim outright, which is what stops an Event the system generated from being
    fed back into extraction and generating another.
    """
    response = api.post(
        "/api/v1/events",
        json={
            "type": "SYSTEM_ACTIVITY",
            "occurred_at": NOW.isoformat(),
            "body_text": MESSAGE,
            "source_system": CHANNEL,
            "source_ref": vendor_message_id(),
        },
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert response.json()["rule"] == "BR-E-13"


def test_the_database_refuses_a_second_original_whatever_the_caller_does(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The backstop under ADR-0058 §3, asserted rather than assumed.

    The derived key gives a *clean* redelivery — the caller gets the original answer back. But a
    contract a connector has to keep is only as good as what happens when it does not, and
    `ux_event_source_ref` is what makes BR-E-02 true regardless: a partial unique index over
    `(org_id, source_system, source_ref)` for rows that are originals and not deleted.

    Written as a direct insert because no application path can reach this state — which is the
    point. Two concurrent deliveries that both skipped the header would race here, and one of them
    would lose.
    """
    reference = vendor_message_id()
    first = deliver(api, as_admin, source_ref=reference).json()

    scoped_session.rollback()
    with pytest.raises(IntegrityError) as collision:
        scoped_session.execute(
            text(
                "INSERT INTO event (id, org_id, source_system, source_ref, content_hash, origin, "
                " type, occurred_at, observed_at, body_text, sensitivity, processing_status) "
                "SELECT :new_id, org_id, source_system, source_ref, content_hash, origin, type, "
                " occurred_at, observed_at, body_text, sensitivity, processing_status "
                "FROM event WHERE id = :id"
            ),
            {"new_id": uuid.uuid4(), "id": uuid.UUID(first["id"])},
        )
    assert "ux_event_source_ref" in str(collision.value)
    scoped_session.rollback()


def test_a_revision_is_allowed_to_share_the_reference(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """And the index is partial for exactly that reason.

    An edit carries the original's reference by design. What is forbidden is a second *original*,
    not a correction — so the index excludes rows that name a `revision_of_event_id`.
    """
    reference = vendor_message_id()
    original = deliver(api, as_admin, source_ref=reference).json()
    first_edit = deliver(api, as_admin, source_ref=reference, body=f"{MESSAGE} Make it Monday.")
    second_edit = deliver(api, as_admin, source_ref=reference, body=f"{MESSAGE} Make it Tuesday.")

    assert first_edit.status_code == 201, first_edit.text
    assert second_edit.status_code == 201, second_edit.text
    assert first_edit.json()["revision_of_event_id"] == original["id"]
    assert second_edit.json()["revision_of_event_id"] == original["id"]
