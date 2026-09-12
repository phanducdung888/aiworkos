"""Commitments over HTTP (BR-C-01 to BR-C-10).

The thing this file is really testing is that a Commitment stays a record of what somebody said,
rather than drifting into a task with a different name. Two behaviours carry that:

A deadline cannot be edited. Moving it is a renegotiation, which needs a new date and keeps the old
one (BR-C-07) — so a promise whose date changed always shows that it changed and to what.

Status moves only along BR-C-04's transitions, and only for the people BR-C-09 names. There is no
PATCH of a status field, because a settable status is a lifecycle anybody can skip through.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import Realm, WorkOrg, auth, grant, subject_of

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
FRIDAY = dt.date(2026, 9, 18)


def a_commitment(api: TestClient, headers: dict[str, str], org: WorkOrg, **over: object) -> dict:
    payload: dict[str, object] = {
        "statement": "I will send the revised quote",
        "committed_by_person_id": str(org.member),
        "due_date": FRIDAY.isoformat(),
        "due_precision": "exact",
    }
    payload.update(over)
    response = api.post("/api/v1/commitments", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def move(
    api: TestClient, headers: dict[str, str], commitment: dict, target: str, **body: object
) -> object:
    payload: dict[str, object] = {"target": target}
    payload.update(body)
    return api.post(
        f"/api/v1/commitments/{commitment['id']}/status",
        json=payload,
        headers={**headers, "If-Match": f'W/"{commitment["version"]}"'},
    )


def reread(api: TestClient, headers: dict[str, str], commitment_id: str) -> dict:
    return api.get(f"/api/v1/commitments/{commitment_id}", headers=headers).json()


# --------------------------------------------------------------------------- creation


def test_a_promise_starts_captured(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Unconfirmed, whoever wrote it down. Opening it is an acknowledgement somebody makes."""
    commitment = a_commitment(api, as_member, work_org)
    assert commitment["status"] == "captured"
    assert commitment["acknowledged_at"] is None

    scoped_session.rollback()
    entries = scoped_session.execute(
        text("SELECT action FROM audit_entry WHERE resource_id = :id"),
        {"id": uuid.UUID(commitment["id"])},
    ).scalars().all()
    assert list(entries) == ["create"]
    events = scoped_session.execute(
        text("SELECT type FROM outbox WHERE aggregate_id = :id"),
        {"id": uuid.UUID(commitment["id"])},
    ).scalars().all()
    assert list(events) == ["CommitmentCreated"]


def test_a_promise_to_the_room_names_nobody(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    commitment = a_commitment(api, as_member, work_org)
    assert commitment["committed_to_person_id"] is None
    assert commitment["committed_to_team_id"] is None


def test_a_promise_to_a_person_and_a_team_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    response = api.post(
        "/api/v1/commitments",
        json={
            "statement": "x",
            "committed_by_person_id": str(work_org.member),
            "committed_to_person_id": str(work_org.team_lead),
            "committed_to_team_id": str(work_org.team_id),
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-C-02" in response.text


def test_a_precise_promise_without_a_date_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    response = api.post(
        "/api/v1/commitments",
        json={
            "statement": "x",
            "committed_by_person_id": str(work_org.member),
            "due_precision": "exact",
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-C-01" in response.text


def test_a_committer_from_another_organization_is_a_named_field_error(
    api: TestClient, as_member: dict[str, str], roles: None, other_org_person: uuid.UUID
) -> None:
    response = api.post(
        "/api/v1/commitments",
        json={"statement": "x", "committed_by_person_id": str(other_org_person)},
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-G-01" in response.text
    assert "committed_by_person_id" in response.text


# --------------------------------------------------------------------------- lifecycle


def test_acknowledging_opens_a_promise(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    commitment = a_commitment(api, as_member, work_org)
    response = move(api, as_member, commitment, "open")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "open"
    assert response.json()["acknowledged_at"] is not None


def test_a_captured_promise_cannot_jump_to_fulfilled(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    commitment = a_commitment(api, as_member, work_org)
    response = move(api, as_member, commitment, "fulfilled")
    assert response.status_code == 422
    assert "BR-C-04" in response.text


def test_a_disputed_promise_is_retained_and_goes_no_further(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-C-05. The named committer says they did not promise that, and that is itself signal."""
    commitment = a_commitment(api, as_member, work_org)
    disputed = move(api, as_member, commitment, "disputed").json()
    assert disputed["status"] == "disputed"

    onward = move(api, as_member, disputed, "open")
    assert onward.status_code == 422

    still_there = reread(api, as_member, commitment["id"])
    assert still_there["statement"] == "I will send the revised quote"


def test_renegotiation_keeps_the_old_date(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-C-07. A promise whose deadline moved always shows that it moved, and from what."""
    commitment = a_commitment(api, as_member, work_org)
    opened = move(api, as_member, commitment, "open").json()
    later = (FRIDAY + dt.timedelta(days=7)).isoformat()

    renegotiated = move(api, as_member, opened, "renegotiated", new_due_date=later)
    assert renegotiated.status_code == 200, renegotiated.text
    body = renegotiated.json()
    assert body["due_date"] == later
    assert body["previous_due_date"] == FRIDAY.isoformat()


def test_renegotiating_without_a_new_date_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    commitment = a_commitment(api, as_member, work_org)
    opened = move(api, as_member, commitment, "open").json()
    response = move(api, as_member, opened, "renegotiated")
    assert response.status_code == 422
    assert "BR-C-07" in response.text


def test_a_deadline_cannot_be_edited(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """There is no `due_date` on the update schema. Moving a date is a renegotiation."""
    commitment = a_commitment(api, as_member, work_org)
    response = api.patch(
        f"/api/v1/commitments/{commitment['id']}",
        json={"due_date": "2026-12-25"},
        headers={**as_member, "If-Match": f'W/"{commitment["version"]}"'},
    )
    assert response.status_code == 422


def test_a_missed_promise_can_still_be_kept(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    commitment = a_commitment(api, as_member, work_org)
    opened = move(api, as_member, commitment, "open").json()
    missed = move(api, as_member, opened, "missed").json()
    fulfilled = move(api, as_member, missed, "fulfilled")
    assert fulfilled.status_code == 200
    assert fulfilled.json()["status"] == "fulfilled"


def test_a_stale_version_loses(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    commitment = a_commitment(api, as_member, work_org)
    first = move(api, as_member, commitment, "open")
    assert first.status_code == 200
    second = move(api, as_member, commitment, "disputed")
    assert second.status_code == 412


# --------------------------------------------------------------------------- BR-C-09


def test_a_bystander_cannot_change_a_promise(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    commitment = a_commitment(api, as_member, work_org)
    outsider = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    response = move(api, outsider, commitment, "open")
    assert response.status_code in (403, 422)


def test_the_recipient_may_change_it(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-C-09 names them, and a promise made to you is one you can say was kept."""
    commitment = a_commitment(
        api, as_member, work_org, committed_to_person_id=str(work_org.outsider)
    )
    recipient = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    response = move(api, recipient, commitment, "open")
    assert response.status_code == 200, response.text


def test_everyone_can_read_a_promise(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Accountability visible only to its parties is not accountability."""
    commitment = a_commitment(api, as_member, work_org)
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "viewer")
    viewer = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    assert api.get(f"/api/v1/commitments/{commitment['id']}", headers=viewer).status_code == 200


# --------------------------------------------------------------------------- BR-C-06


def test_overdue_reports_without_writing(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """`missed` is a transition with an actor, so a read must not perform one."""
    commitment = a_commitment(
        api, as_member, work_org, due_date=(dt.date.today() - dt.timedelta(days=3)).isoformat()
    )
    move(api, as_member, commitment, "open")

    overdue = api.get("/api/v1/commitments/overdue/today", headers=as_member).json()["items"]
    assert any(row["id"] == commitment["id"] for row in overdue)

    scoped_session.rollback()
    status = scoped_session.execute(
        text("SELECT status FROM commitment WHERE id = :id"),
        {"id": uuid.UUID(commitment["id"])},
    ).scalar_one()
    assert status == "open", "reading overdue commitments must not change any of them"


def test_a_vague_promise_is_never_overdue(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-C-06. "Soon" has no deadline to miss; it ages into a review prompt instead."""
    commitment = a_commitment(
        api,
        as_member,
        work_org,
        due_date=(dt.date.today() - dt.timedelta(days=90)).isoformat(),
        due_precision="vague",
    )
    move(api, as_member, commitment, "open")
    overdue = api.get("/api/v1/commitments/overdue/today", headers=as_member).json()["items"]
    assert all(row["id"] != commitment["id"] for row in overdue)


# --------------------------------------------------------------------------- provenance


def test_a_promise_can_name_the_event_it_was_made_in(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    event = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": NOW.isoformat(),
            "body_text": "I will send the revised quote",
        },
        headers=as_member,
    ).json()
    commitment = a_commitment(api, as_member, work_org, origin_event_id=event["id"])
    assert commitment["origin_event_id"] == event["id"]

    listed = api.get(
        f"/api/v1/commitments?origin_event_id={event['id']}", headers=as_member
    ).json()["items"]
    assert [row["id"] for row in listed] == [commitment["id"]]


def test_another_organizations_commitment_is_invisible(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    assert api.get(f"/api/v1/commitments/{uuid.uuid4()}", headers=as_member).status_code == 404
