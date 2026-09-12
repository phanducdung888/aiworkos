"""Attachments (ADR-0039).

The design claim being tested is narrow and load-bearing: *attachment authorization is Event
authorization*. There is no attachment permission, so there is nothing for the two to disagree
about — but only if every endpoint really does load the Event first. These tests are mostly attempts
to reach an attachment by a route that skips that step.

The store is the in-memory implementation of the real protocol, and uploads go through the presigned
URL the API issued rather than by writing a key directly. A test that reached past the URL would
prove the metadata bookkeeping works and nothing about whether the flow does.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.storage import InMemoryObjectStore, object_key_for
from tests.integration.conftest import Realm, WorkOrg, auth, grant, subject_of

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
CONTENT = b"%PDF-1.7 a quote, in bytes"


def an_event(api: TestClient, headers: dict[str, str], **over: object) -> dict:
    payload: dict[str, object] = {
        "type": "MANUAL_CAPTURE",
        "occurred_at": NOW.isoformat(),
        "body_text": "quote attached",
    }
    payload.update(over)
    response = api.post("/api/v1/events", json=payload, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()


def start(api: TestClient, headers: dict[str, str], event_id: str) -> dict:
    response = api.post(
        f"/api/v1/events/{event_id}/attachments",
        json={"filename": "quote.pdf", "media_type": "application/pdf"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(store: InMemoryObjectStore, ticket: dict, content: bytes = CONTENT) -> None:
    store.write_through(ticket["upload_url"], content, "application/pdf")


# --------------------------------------------------------------------------- the happy path


def test_the_two_step_flow_records_what_the_store_holds(
    api: TestClient, as_member: dict[str, str], roles: None, object_store: InMemoryObjectStore
) -> None:
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    assert ticket["attachment"]["status"] == "pending"
    assert ticket["attachment"]["size_bytes"] is None

    upload(object_store, ticket)
    completed = api.post(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "available"
    # The store's number, not the client's — nothing in either request said how big the file was.
    assert body["size_bytes"] == len(CONTENT)
    assert body["checksum"]


def test_the_api_never_sees_the_bytes(
    api: TestClient, as_member: dict[str, str], roles: None, object_store: InMemoryObjectStore,
    scoped_session: Session,
) -> None:
    """ADR-0039's other half: Postgres holds metadata, the store holds content."""
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    upload(object_store, ticket)
    api.post(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )
    scoped_session.rollback()
    stored = scoped_session.execute(
        text("SELECT * FROM event_attachment WHERE id = :id"),
        {"id": uuid.UUID(ticket["attachment"]["id"])},
    ).mappings().one()
    assert not any(
        isinstance(value, (bytes, memoryview)) for value in stored.values()
    ), "no column of event_attachment may hold content"


def test_a_client_that_never_uploads_leaves_a_pending_row(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """Visible as unfinished, rather than claiming a file nobody can fetch."""
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    completed = api.post(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )
    assert completed.status_code == 422
    assert "BR-E-14" in completed.text

    detail = api.get(f"/api/v1/events/{event['id']}", headers=as_member).json()
    assert detail["attachments"][0]["status"] == "pending"


def test_completing_twice_is_the_client_retrying(
    api: TestClient, as_member: dict[str, str], roles: None, object_store: InMemoryObjectStore
) -> None:
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    upload(object_store, ticket)
    url = f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete"
    first = api.post(url, headers=as_member)
    second = api.post(url, headers=as_member)
    assert first.status_code == second.status_code == 200
    assert first.json()["checksum"] == second.json()["checksum"]


def test_a_pending_attachment_cannot_be_downloaded(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    response = api.get(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/content",
        headers=as_member,
    )
    assert response.status_code == 422


def test_a_download_url_points_at_the_uploaded_object(
    api: TestClient, as_member: dict[str, str], roles: None, object_store: InMemoryObjectStore,
    work_org: WorkOrg,
) -> None:
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    upload(object_store, ticket)
    api.post(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )
    content = api.get(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/content",
        headers=as_member,
    )
    assert content.status_code == 200
    expected = object_key_for(
        org_id=work_org.org_id,
        event_id=uuid.UUID(event["id"]),
        attachment_id=uuid.UUID(ticket["attachment"]["id"]),
    )
    assert object_store.key_for_get(content.json()["download_url"]) == expected


def test_the_object_key_is_never_published(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """It is derived server-side and a client has no business reasoning about bucket layout."""
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    assert "object_key" not in ticket["attachment"]
    detail = api.get(f"/api/v1/events/{event['id']}", headers=as_member).json()
    assert "object_key" not in detail["attachments"][0]


# --------------------------------------------------------------------------- authorization


def test_attaching_to_somebody_elses_event_is_refused(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """A member reaches their own captures. Adding a file to another person's record of what
    happened is a change to their record, and `EVENT.ATTACH` gives a member `PERSONAL` only."""
    event = an_event(api, as_member)
    other = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    response = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "x.pdf", "media_type": "application/pdf"},
        headers=other,
    )
    assert response.status_code == 403


def test_an_admin_may_attach_to_any_event_in_the_organization(
    api: TestClient, as_member: dict[str, str], as_admin: dict[str, str], roles: None
) -> None:
    event = an_event(api, as_member)
    response = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "x.pdf", "media_type": "application/pdf"},
        headers=as_admin,
    )
    assert response.status_code == 201, response.text


def test_an_attachment_on_a_restricted_event_follows_the_event(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    object_store: InMemoryObjectStore, scoped_session: Session,
) -> None:
    """The claim ADR-0039 rests on, tested where it would break.

    The attachment id is known to the caller below — they were told it by nobody, but a guessable
    or leaked id must not be enough. Reaching the content requires reaching the Event.
    """
    event = an_event(
        api,
        as_member,
        sensitivity="restricted",
        participants=[{"role": "speaker", "person_id": str(work_org.member)}],
    )
    ticket = start(api, as_member, event["id"])
    upload(object_store, ticket)
    api.post(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )

    outsider = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    content = api.get(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/content",
        headers=outsider,
    )
    # 404: the Event is not readable, so neither it nor anything hanging off it exists to them.
    assert content.status_code == 404


def test_an_attachment_cannot_be_reached_through_a_different_event(
    api: TestClient, as_member: dict[str, str], as_admin: dict[str, str], roles: None,
    object_store: InMemoryObjectStore,
) -> None:
    """The path names both. Authorizing the Event and then trusting the attachment id would let a
    caller pair an Event they may read with an attachment they may not."""
    private = an_event(api, as_member, body_text="private")
    ticket = start(api, as_member, private["id"])
    upload(object_store, ticket)
    api.post(
        f"/api/v1/events/{private['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )

    decoy = an_event(api, as_admin, body_text="decoy")
    response = api.get(
        f"/api/v1/events/{decoy['id']}/attachments/{ticket['attachment']['id']}/content",
        headers=as_admin,
    )
    assert response.status_code == 404


def test_another_organizations_attachment_is_unreachable(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    event = an_event(api, as_member)
    response = api.get(
        f"/api/v1/events/{event['id']}/attachments/{uuid.uuid4()}/content", headers=as_member
    )
    assert response.status_code == 404


def test_a_viewer_cannot_attach(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    event = an_event(api, as_member)
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "viewer")
    viewer = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    response = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "x.pdf", "media_type": "application/pdf"},
        headers=viewer,
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- audit and outbox


def test_the_attachment_flow_is_audited_against_the_event(
    api: TestClient, as_member: dict[str, str], roles: None, object_store: InMemoryObjectStore,
    scoped_session: Session,
) -> None:
    event = an_event(api, as_member)
    ticket = start(api, as_member, event["id"])
    upload(object_store, ticket)
    api.post(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )
    scoped_session.rollback()
    actions = [
        row[0]
        for row in scoped_session.execute(
            text(
                "SELECT action FROM audit_entry WHERE resource_id = :id ORDER BY occurred_at"
            ),
            {"id": uuid.UUID(event["id"])},
        ).all()
    ]
    assert actions == ["create", "attach", "attach"]

    types = [
        row[0]
        for row in scoped_session.execute(
            text("SELECT type FROM outbox WHERE aggregate_id = :id ORDER BY occurred_at"),
            {"id": uuid.UUID(event["id"])},
        ).all()
    ]
    assert types == [
        "EventCaptured",
        "EventAttachmentStarted",
        "EventAttachmentCompleted",
    ]


def test_a_retried_start_reserves_one_attachment(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    event = an_event(api, as_member)
    headers = {**as_member, "Idempotency-Key": uuid.uuid4().hex}
    body = {"filename": "quote.pdf", "media_type": "application/pdf"}
    first = api.post(f"/api/v1/events/{event['id']}/attachments", json=body, headers=headers)
    second = api.post(f"/api/v1/events/{event['id']}/attachments", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["attachment"]["id"] == second.json()["attachment"]["id"]

    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM event_attachment WHERE event_id = :id"),
        {"id": uuid.UUID(event["id"])},
    ).scalar_one() == 1
