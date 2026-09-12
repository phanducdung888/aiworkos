"""The capture surface over HTTP.

Four questions, the same four the Work and Identity slices answer: does the mutation take the whole
path, is the read filtered rather than trimmed, does the idempotency story hold, and does another
organization's row look like nothing at all.

The fifth question belongs to this slice alone and most of the file is about it: an Event is a
record of something that happened, so the interesting cases are all the ones where writing the
obvious code would quietly rewrite history instead of adding to it.
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


def capture(api: TestClient, headers: dict[str, str], **over: object) -> dict:
    payload: dict[str, object] = {
        "type": "MANUAL_CAPTURE",
        "occurred_at": NOW.isoformat(),
        "body_text": "Nam said he would send the revised quote on Friday.",
    }
    payload.update(over)
    response = api.post("/api/v1/events", json=payload, headers=headers)
    assert response.status_code in (200, 201), response.text
    return response.json()


def audit_rows(session: Session, resource_id: uuid.UUID) -> list[dict]:
    session.rollback()
    return [
        dict(row)
        for row in session.execute(
            text(
                "SELECT action, resource_type, actor, authorization_context, after_state "
                "FROM audit_entry WHERE resource_id = :id ORDER BY occurred_at"
            ),
            {"id": resource_id},
        )
        .mappings()
        .all()
    ]


def outbox_rows(session: Session, aggregate_id: uuid.UUID) -> list[dict]:
    session.rollback()
    return [
        dict(row)
        for row in session.execute(
            text(
                "SELECT type, payload FROM outbox WHERE aggregate_id = :id ORDER BY occurred_at"
            ),
            {"id": aggregate_id},
        )
        .mappings()
        .all()
    ]


# --------------------------------------------------------------------------- capture


def test_capturing_an_event_takes_the_whole_path(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    event = capture(api, as_member, title="Call with Nam")
    assert event["origin"] == "external"
    assert event["processing_status"] == "received"
    # BR-E-13: the capturer, which is not a claim about who authored the content.
    assert event["captured_by_person_id"] == str(work_org.member)

    entries = audit_rows(scoped_session, uuid.UUID(event["id"]))
    assert [e["action"] for e in entries] == ["create"]
    assert entries[0]["resource_type"] == "event"
    assert entries[0]["actor"]["person_id"] == str(work_org.member)

    events = outbox_rows(scoped_session, uuid.UUID(event["id"]))
    assert [e["type"] for e in events] == ["EventCaptured"]
    # BR-E-11 is stated on the message so a consumer never re-derives it.
    assert events[0]["payload"]["extractable"] is True


def test_the_audit_trail_does_not_copy_the_captured_content(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """A second permanent copy of every captured message, in a table retention cannot purge.

    BR-E-07 lets an Event's payload be purged. An audit row holding the same text would survive
    that purge and cannot be edited, so the deletion would be partial in a way nobody could fix.
    """
    secret = "the number is 0900-111-222"
    event = capture(api, as_member, body_text=secret)
    entries = audit_rows(scoped_session, uuid.UUID(event["id"]))
    assert secret not in str(entries[0]["after_state"])


def test_a_viewer_cannot_capture(
    api: TestClient, realm: Realm, roles: None, work_org: WorkOrg, scoped_session: Session
) -> None:
    # `dept_lead` rather than `outsider`: the shared `roles` fixture already makes the outsider a
    # member, and a person holding both roles is allowed to capture — which would make this test
    # pass for a reason that has nothing to do with viewers.
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "viewer")
    headers = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    response = api.post(
        "/api/v1/events",
        json={"type": "MANUAL_CAPTURE", "occurred_at": NOW.isoformat(), "body_text": "x"},
        headers=headers,
    )
    assert response.status_code == 403


def test_an_event_with_no_content_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    response = api.post(
        "/api/v1/events",
        json={"type": "MANUAL_CAPTURE", "occurred_at": NOW.isoformat(), "body_text": "   "},
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-03" in response.text


def test_an_occurrence_far_in_the_future_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    future = dt.datetime.now(dt.UTC) + dt.timedelta(days=3)
    response = api.post(
        "/api/v1/events",
        json={"type": "MANUAL_CAPTURE", "occurred_at": future.isoformat(), "body_text": "x"},
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-09" in response.text


def test_a_person_cannot_fabricate_system_activity(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """BR-E-13. SYSTEM_ACTIVITY is the system's account of itself."""
    response = api.post(
        "/api/v1/events",
        json={"type": "SYSTEM_ACTIVITY", "occurred_at": NOW.isoformat(), "body_text": "x"},
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-13" in response.text


def test_a_comment_cannot_be_captured_as_external(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """BR-E-15. A comment is internal by definition, and this API only writes external Events."""
    response = api.post(
        "/api/v1/events",
        json={"type": "COMMENT", "occurred_at": NOW.isoformat(), "body_text": "looks good"},
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-15" in response.text


# --------------------------------------------------------------------------- BR-E-02


def test_the_same_reference_twice_returns_the_same_event(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    ref = f"msg-{uuid.uuid4().hex[:8]}"
    first = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": "the quote is attached",
            "source_system": "openclaw.whatsapp",
            "source_ref": ref,
        },
        headers=as_member,
    )
    assert first.status_code == 201

    second = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": "the quote is attached",
            "source_system": "openclaw.whatsapp",
            "source_ref": ref,
        },
        headers=as_member,
    )
    # 200, not 201 and not 409: the caller is being told about an Event that already exists.
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]

    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM event WHERE source_ref = :ref"), {"ref": ref}
    ).scalar_one() == 1


def test_changed_content_under_the_same_reference_creates_a_revision(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """BR-E-02, and the heart of "a correction is a new Event".

    The original keeps saying what it originally said. That is the property the whole immutability
    design exists to protect, and this is the path a user can actually reach it through.
    """
    ref = f"msg-{uuid.uuid4().hex[:8]}"
    original = capture(
        api, as_member, type="EXTERNAL_MESSAGE", source_ref=ref,
        source_system="openclaw.whatsapp", body_text="Friday",
    )
    revision = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": "Monday, sorry",
            "source_system": "openclaw.whatsapp",
            "source_ref": ref,
        },
        headers=as_member,
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()["id"] != original["id"]
    assert revision.json()["revision_of_event_id"] == original["id"]

    unchanged = api.get(f"/api/v1/events/{original['id']}", headers=as_member)
    assert unchanged.json()["body_text"] == "Friday", "history must not be rewritten"
    assert unchanged.json()["revision_of_event_id"] is None

    assert [e["type"] for e in outbox_rows(scoped_session, uuid.UUID(revision.json()["id"]))] == [
        "EventRevised"
    ]


def test_two_manual_captures_of_the_same_text_are_two_events(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """No source_ref means no dedup. Two people writing down one meeting wrote down two
    observations, and collapsing them is a judgement the system may not make from a hash."""
    first = capture(api, as_member, body_text="same words")
    second = capture(api, as_member, body_text="same words")
    assert first["id"] != second["id"]


def test_a_retried_request_replays_rather_than_capturing_twice(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """HTTP idempotency, which is a different mechanism from BR-E-02 and also applies.

    Here there is no `source_ref` at all, so BR-E-02 would happily create two Events. The
    `Idempotency-Key` is what makes the retry safe.
    """
    headers = {**as_member, "Idempotency-Key": uuid.uuid4().hex}
    body = {
        "type": "MANUAL_CAPTURE",
        "occurred_at": NOW.isoformat(),
        "body_text": "captured once",
    }
    first = api.post("/api/v1/events", json=body, headers=headers)
    second = api.post("/api/v1/events", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


# --------------------------------------------------------------------------- participants


def test_participants_are_recorded_resolved_and_unresolved(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    event = capture(
        api,
        as_member,
        participants=[
            {"role": "speaker", "person_id": str(work_org.team_lead), "confidence": 90},
            {"role": "mentioned", "external_handle": "+84900000001"},
        ],
    )
    assert event["participant_count"] == 2
    by_role = {p["role"]: p for p in event["participants"]}
    assert by_role["speaker"]["person_id"] == str(work_org.team_lead)
    assert by_role["speaker"]["resolved_at"] is not None
    # An unresolved handle must look unresolved, or a later resolution step has nothing to do.
    assert by_role["mentioned"]["person_id"] is None
    assert by_role["mentioned"]["resolved_at"] is None
    assert by_role["mentioned"]["external_handle"] == "+84900000001"


def test_a_participant_from_another_organization_is_a_named_field_error(
    api: TestClient, as_member: dict[str, str], roles: None, other_org_person: uuid.UUID
) -> None:
    """ADR-0035. Not a foreign key violation arriving from psycopg several layers later."""
    response = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": NOW.isoformat(),
            "body_text": "x",
            "participants": [{"role": "speaker", "person_id": str(other_org_person)}],
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-G-01" in response.text
    assert "participants[0].person_id" in response.text


def test_a_participant_naming_nobody_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    response = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": NOW.isoformat(),
            "body_text": "x",
            "participants": [{"role": "speaker"}],
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-12" in response.text


# --------------------------------------------------------------------------- BR-E-08


def test_a_restricted_event_is_absent_for_a_non_participant(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Absent, not forbidden. A 403 would confirm it exists, which is most of the secret."""
    event = capture(
        api,
        as_member,
        sensitivity="restricted",
        body_text="salary discussion",
        participants=[{"role": "speaker", "person_id": str(work_org.member)}],
    )
    outsider = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)

    assert api.get(f"/api/v1/events/{event['id']}", headers=outsider).status_code == 404
    listed = api.get("/api/v1/events", headers=outsider).json()["items"]
    assert all(row["id"] != event["id"] for row in listed)


def test_a_restricted_event_reaches_its_participant(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    event = capture(
        api,
        as_member,
        sensitivity="restricted",
        body_text="salary discussion",
        participants=[{"role": "speaker", "person_id": str(work_org.team_lead)}],
    )
    participant = auth(realm, subject_of(scoped_session, work_org.team_lead), work_org.org_id)
    assert api.get(f"/api/v1/events/{event['id']}", headers=participant).status_code == 200


def test_a_restricted_event_reaches_an_auditor(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-E-08 names the auditor and nobody else."""
    event = capture(api, as_member, sensitivity="restricted", body_text="salary discussion")
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "auditor")
    auditor = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    assert api.get(f"/api/v1/events/{event['id']}", headers=auditor).status_code == 200


def test_a_restricted_event_is_withheld_from_extraction(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    event = capture(api, as_member, sensitivity="restricted", body_text="salary discussion")
    events = outbox_rows(scoped_session, uuid.UUID(event["id"]))
    assert events[0]["payload"]["extractable"] is False


def test_a_confidential_event_is_still_organization_wide(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Only `restricted` narrows reading. Enforcing a second tier would make `confidential` mean
    something no rule states."""
    event = capture(api, as_member, sensitivity="confidential")
    outsider = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    assert api.get(f"/api/v1/events/{event['id']}", headers=outsider).status_code == 200


# --------------------------------------------------------------------------- listing


def test_the_feed_is_newest_first_and_pages(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    for minute in range(5):
        capture(
            api,
            as_member,
            occurred_at=(NOW + dt.timedelta(minutes=minute)).isoformat(),
            body_text=f"note {minute}",
        )
    page = api.get("/api/v1/events?limit=3", headers=as_member).json()
    assert len(page["items"]) == 3
    assert page["next_cursor"] is not None

    occurred = [row["occurred_at"] for row in page["items"]]
    assert occurred == sorted(occurred, reverse=True)

    second = api.get(
        f"/api/v1/events?limit=3&cursor={page['next_cursor']}", headers=as_member
    ).json()
    first_ids = {row["id"] for row in page["items"]}
    assert all(row["id"] not in first_ids for row in second["items"])


def test_the_feed_filters_by_participant(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    wanted = capture(
        api,
        as_member,
        body_text="about the lead",
        participants=[{"role": "mentioned", "person_id": str(work_org.team_lead)}],
    )
    capture(api, as_member, body_text="about nobody")
    listed = api.get(
        f"/api/v1/events?participant_person_id={work_org.team_lead}", headers=as_member
    ).json()["items"]
    assert [row["id"] for row in listed] == [wanted["id"]]


# --------------------------------------------------------------------------- tenancy


def test_another_organizations_event_looks_like_nothing_at_all(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None,
    work_org: WorkOrg, app_session_factory, scoped_session: Session,
) -> None:
    from app.platform.ids import uuid7

    other_org, other_event = uuid7(), uuid7()
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
            "INSERT INTO event (id, org_id, source_system, content_hash, origin, type, "
            "occurred_at, body_text) VALUES (:id, :org, 'web', 'h', 'external', "
            "'MANUAL_CAPTURE', now(), 'their private note')"
        ),
        {"id": other_event, "org": other_org},
    )
    session.commit()
    session.close()

    assert api.get(f"/api/v1/events/{other_event}", headers=as_member).status_code == 404
    listed = api.get("/api/v1/events", headers=as_member).json()["items"]
    assert all(row["id"] != str(other_event) for row in listed)
