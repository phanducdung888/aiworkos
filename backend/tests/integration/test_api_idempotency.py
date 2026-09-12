"""Retrying a POST must not create a second entity.

The assertion that matters is not that the second response looks the same. It is that the second
request left nothing behind: one row, one audit entry, one outbox event. A replay that returns the
right body while quietly writing a duplicate audit entry has made the system harder to reason about,
not safer, and the response body alone would never show it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration


def counts(session: Session, work_id: uuid.UUID) -> tuple[int, int, int]:
    session.rollback()
    work = session.execute(
        text("SELECT count(*) FROM work WHERE id = :id"), {"id": work_id}
    ).scalar_one()
    audit = session.execute(
        text("SELECT count(*) FROM audit_entry WHERE resource_id = :id"), {"id": work_id}
    ).scalar_one()
    outbox = session.execute(
        text("SELECT count(*) FROM outbox WHERE aggregate_id = :id"), {"id": work_id}
    ).scalar_one()
    return int(work), int(audit), int(outbox)


def test_a_replayed_post_creates_nothing_the_second_time(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """The whole point of W-6, asserted past the response body."""
    headers = {**as_member, "Idempotency-Key": "checkout-7f3a"}
    payload = {"title": "Check the IOC API"}

    first = api.post("/api/v1/work", json=payload, headers=headers)
    assert first.status_code == 201
    work_id = uuid.UUID(first.json()["id"])
    assert counts(scoped_session, work_id) == (1, 1, 1)

    second = api.post("/api/v1/work", json=payload, headers=headers)
    assert second.status_code == 201
    assert second.json() == first.json(), "the replay is the first answer, not a new one"
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.headers["ETag"] == first.headers["ETag"]

    assert counts(scoped_session, work_id) == (1, 1, 1), (
        "a replay that writes a second audit entry or outbox event is not idempotent"
    )
    total = scoped_session.execute(
        text("SELECT count(*) FROM work WHERE title = :title"), {"title": payload["title"]}
    ).scalar_one()
    assert total == 1


def test_the_same_key_with_a_different_body_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """Answering a different question with the first reply would discard the second request."""
    headers = {**as_member, "Idempotency-Key": "one-key-one-operation"}
    first = api.post("/api/v1/work", json={"title": "First"}, headers=headers)
    assert first.status_code == 201

    second = api.post("/api/v1/work", json={"title": "Different"}, headers=headers)
    assert second.status_code == 422
    assert second.json()["type"] == "urn:workos:error:idempotency-key-reused"

    scoped_session.rollback()
    assert (
        scoped_session.execute(
            text("SELECT count(*) FROM work WHERE title = 'Different'")
        ).scalar_one()
        == 0
    ), "the refused request must not have created anything"


def test_field_order_does_not_make_a_retry_look_new(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """A client re-serialising from a dict must not be punished for key ordering."""
    headers = {**as_member, "Idempotency-Key": "order-insensitive"}
    first = api.post(
        "/api/v1/work", json={"title": "Ordered", "priority": "high"}, headers=headers
    )
    second = api.post(
        "/api/v1/work", json={"priority": "high", "title": "Ordered"}, headers=headers
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


def test_the_same_key_on_a_different_endpoint_is_a_different_record(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """One key per user action should not require knowing which endpoints that action hits."""
    headers = {**as_admin, "Idempotency-Key": "one-action-many-calls"}
    work = api.post("/api/v1/work", json={"title": "Shared"}, headers=headers)
    assert work.status_code == 201

    assignment = api.post(
        f"/api/v1/work/{work.json()['id']}/assignments",
        json={"person_id": str(work_org.member)},
        headers=headers,
    )
    assert assignment.status_code == 201, assignment.text
    assert assignment.json()["work_id"] == work.json()["id"]


def test_a_post_without_a_key_behaves_exactly_as_before(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """The header is optional, so a client that has never heard of it is unaffected."""
    payload = {"title": "Twice on purpose"}
    first = api.post("/api/v1/work", json=payload, headers=as_member)
    second = api.post("/api/v1/work", json=payload, headers=as_member)
    assert first.json()["id"] != second.json()["id"]

    scoped_session.rollback()
    assert (
        scoped_session.execute(
            text("SELECT count(*) FROM work WHERE title = :t"), {"t": payload["title"]}
        ).scalar_one()
        == 2
    )


def test_a_stored_key_is_scoped_to_its_organization(
    api: TestClient,
    as_member: dict[str, str],
    roles: None,
    scoped_session: Session,
    work_org: WorkOrg,
) -> None:
    """RLS covers it like any table. A key is one organization's request, and so is its body."""
    headers = {**as_member, "Idempotency-Key": "tenant-scoped"}
    api.post("/api/v1/work", json={"title": "Scoped"}, headers=headers)

    scoped_session.rollback()
    rows = scoped_session.execute(
        text("SELECT org_id, endpoint FROM idempotency_key WHERE key = 'tenant-scoped'")
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["org_id"] == work_org.org_id
    assert rows[0]["endpoint"] == "POST /api/v1/work"
