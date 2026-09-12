"""Capture writes four things or none of them.

An Event, its participants, an audit entry and an outbox row are written in one transaction
(ADR-0004). The failure this guards against is not "the write failed" — that is fine and visible —
but the half-write: an Event that exists with nothing recording who created it, or an outbox row
announcing an Event that was rolled back, which a consumer would then fail to find forever.

The failures here are induced by real integrity violations rather than by patching, because what is
being tested is that the transaction boundary is where it looks like it is. A mocked exception
proves the `except` clause runs; a duplicate key proves the database and the application agree about
what one unit of work is.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.storage import InMemoryObjectStore
from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)


def counts(session: Session, org_id: uuid.UUID) -> dict[str, int]:
    session.rollback()
    return {
        table: session.execute(
            text(f"SELECT count(*) FROM {table} WHERE org_id = :org"), {"org": org_id}
        ).scalar_one()
        for table in ("event", "event_participant", "event_attachment", "audit_entry", "outbox")
    }


def test_a_failed_capture_leaves_no_event_no_audit_and_no_outbox_row(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The same person twice in the same role violates a partial unique index.

    It fires *after* the Event row, the first participant, the audit entry and the outbox row are
    all in the transaction, which is exactly the window a half-write would open.
    """
    before = counts(scoped_session, work_org.org_id)

    response = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": NOW.isoformat(),
            "body_text": "who said what",
            "participants": [
                {"role": "speaker", "person_id": str(work_org.team_lead)},
                {"role": "speaker", "person_id": str(work_org.team_lead)},
            ],
        },
        headers=as_member,
    )
    assert response.status_code >= 400, "a duplicate participant must not be accepted"
    assert response.status_code != 500, (
        f"an integrity violation reached the client as a server error: {response.text}"
    )

    assert counts(scoped_session, work_org.org_id) == before


def test_a_successful_capture_writes_all_four_together(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The control for the test above. Without it, a capture path that silently wrote nothing
    would pass the rollback test perfectly."""
    before = counts(scoped_session, work_org.org_id)
    response = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": NOW.isoformat(),
            "body_text": "who said what",
            "participants": [{"role": "speaker", "person_id": str(work_org.team_lead)}],
        },
        headers=as_member,
    )
    assert response.status_code == 201, response.text

    after = counts(scoped_session, work_org.org_id)
    assert after["event"] == before["event"] + 1
    assert after["event_participant"] == before["event_participant"] + 1
    assert after["audit_entry"] == before["audit_entry"] + 1
    assert after["outbox"] == before["outbox"] + 1


def test_a_refused_capture_writes_nothing_at_all(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """A rule violation is refused before anything is written, so there is no audit row either.

    Recording attempts that were refused is a real design option and is not this system's: BR-G-03
    audits what happened to the data, and nothing happened here.
    """
    before = counts(scoped_session, work_org.org_id)
    response = api.post(
        "/api/v1/events",
        json={"type": "MANUAL_CAPTURE", "occurred_at": NOW.isoformat(), "body_text": "  "},
        headers=as_member,
    )
    assert response.status_code == 422
    assert counts(scoped_session, work_org.org_id) == before


def test_a_failed_attachment_start_leaves_no_row_and_no_outbox_entry(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    object_store: InMemoryObjectStore, scoped_session: Session,
) -> None:
    """If the store cannot issue a URL, the reservation must not survive.

    A `pending` row pointing at an object that was never even reservable is not recoverable by
    retrying — the retry makes a second row — and it is indistinguishable from an upload the client
    abandoned, so the retention sweep would treat a bug as ordinary litter.
    """
    event = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": NOW.isoformat(),
            "body_text": "quote attached",
        },
        headers=as_member,
    ).json()
    before = counts(scoped_session, work_org.org_id)

    def refuse(*args: object, **kwargs: object) -> str:
        raise RuntimeError("object store unavailable")

    original = object_store.presigned_put
    object_store.presigned_put = refuse  # type: ignore[method-assign]
    try:
        response = api.post(
            f"/api/v1/events/{event['id']}/attachments",
            json={"filename": "quote.pdf", "media_type": "application/pdf"},
            headers=as_member,
        )
    finally:
        object_store.presigned_put = original  # type: ignore[method-assign]

    assert response.status_code == 500, "a store outage is a server error, honestly reported"
    assert counts(scoped_session, work_org.org_id) == before


def test_an_outbox_row_never_describes_an_event_that_does_not_exist(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The invariant a consumer depends on, asserted over everything this suite has written.

    An outbox row naming a missing aggregate is the failure mode that cannot be recovered from
    downstream: the consumer retries, finds nothing, and either drops the message or stalls.
    """
    scoped_session.rollback()
    orphans = scoped_session.execute(
        text(
            "SELECT o.id, o.type FROM outbox o "
            "LEFT JOIN event e ON e.id = o.aggregate_id AND e.org_id = o.org_id "
            "WHERE o.aggregate_type = 'event' AND e.id IS NULL"
        )
    ).all()
    assert not orphans, f"outbox rows describing absent events: {orphans}"
