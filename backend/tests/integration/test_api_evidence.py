"""Evidence over HTTP (BR-E-04 to BR-E-06, BR-E-14).

The rule doing the most work is BR-E-05: the excerpt must be what the Event actually says, at the
locator recorded, checked rather than trusted. Most of this file is attempts to record a citation
that reads plausibly and is not true of its source — a paraphrase, a tidied quote, a locator
pointing somewhere else. Each one has to be refused, because a citation that cannot be verified
against its source is worse than none: it looks like proof.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.storage import InMemoryObjectStore
from tests.integration.conftest import Realm, WorkOrg, auth, grant, subject_of

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
SAID = "Nam will send the revised quote by Friday."


def an_event(api: TestClient, headers: dict[str, str], body: str = SAID) -> dict:
    response = api.post(
        "/api/v1/events",
        json={"type": "MANUAL_CAPTURE", "occurred_at": NOW.isoformat(), "body_text": body},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def cite(
    api: TestClient, headers: dict[str, str], event: dict, quote: str, **over: object
) -> object:
    start = event["body_text"].index(quote) if quote in event["body_text"] else 0
    payload: dict[str, object] = {
        "event_id": event["id"],
        "target_type": "work",
        "target_id": str(uuid.uuid4()),
        "assertion": "supports",
        "text_locator": {"char_start": start, "char_end": start + len(quote)},
        "excerpt": quote,
        "confidence": 80,
    }
    payload.update(over)
    return api.post("/api/v1/evidence", json=payload, headers=headers)


# --------------------------------------------------------------------------- BR-E-05


def test_a_verbatim_quote_is_recorded(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    event = an_event(api, as_member)
    response = cite(api, as_member, event, "revised quote")
    assert response.status_code == 201, response.text
    evidence = response.json()
    assert evidence["excerpt"] == "revised quote"
    assert evidence["claim_summary"] is None
    assert evidence["produced_by_type"] == "person"
    assert evidence["produced_by_id"] == str(work_org.member)

    scoped_session.rollback()
    actions = [
        row[0]
        for row in scoped_session.execute(
            text("SELECT action FROM audit_entry WHERE resource_id = :id"),
            {"id": uuid.UUID(evidence["id"])},
        ).all()
    ]
    assert actions == ["create"]


def test_a_paraphrase_is_not_evidence(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """The failure this rule exists for: it reads well and is not what was said."""
    event = an_event(api, as_member)
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "text_locator": {"char_start": 0, "char_end": 13},
            "excerpt": "Nam promised a quote",
            "confidence": 90,
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-05" in response.text


def test_a_locator_pointing_somewhere_else_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """Checking only that the excerpt appears *somewhere* would accept this.

    A locator that points at an unrelated sentence is worse than no locator: it looks precise.
    """
    event = an_event(api, as_member)
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "text_locator": {"char_start": 0, "char_end": 13},
            "excerpt": "revised quote",
            "confidence": 90,
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-05" in response.text


def test_a_locator_past_the_end_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    event = an_event(api, as_member)
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "text_locator": {"char_start": 0, "char_end": 9999},
            "excerpt": SAID,
            "confidence": 90,
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-05" in response.text


def test_both_a_quote_and_a_summary_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """A reader could not tell which half was checked."""
    event = an_event(api, as_member)
    response = cite(api, as_member, event, "revised quote", claim_summary="a summary too")
    assert response.status_code == 422


def test_a_citation_needs_a_locator(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    event = an_event(api, as_member)
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "excerpt": "revised quote",
        },
        headers=as_member,
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- BR-E-14


def test_an_attachment_is_summarised_not_quoted(
    api: TestClient, as_member: dict[str, str], roles: None, object_store: InMemoryObjectStore
) -> None:
    """A PDF has no character range. Evidence over one carries a claim instead."""
    event = an_event(api, as_member)
    ticket = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "quote.pdf", "media_type": "application/pdf"},
        headers=as_member,
    ).json()
    object_store.write_through(ticket["upload_url"], b"%PDF-1.7", "application/pdf")
    api.post(
        f"/api/v1/events/{event['id']}/attachments/{ticket['attachment']['id']}/complete",
        headers=as_member,
    )

    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "attachment_locator": {"attachment_id": ticket["attachment"]["id"]},
            "claim_summary": "The attached quote shows the revised figure",
            "confidence": 70,
        },
        headers=as_member,
    )
    assert response.status_code == 201, response.text
    assert response.json()["excerpt"] is None
    assert response.json()["claim_summary"]


def test_an_attachment_from_another_event_cannot_be_cited(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """BR-E-04. The citation would claim a file the cited Event never carried."""
    first, second = an_event(api, as_member), an_event(api, as_member, "another thing")
    ticket = api.post(
        f"/api/v1/events/{first['id']}/attachments",
        json={"filename": "q.pdf", "media_type": "application/pdf"},
        headers=as_member,
    ).json()
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": second["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "attachment_locator": {"attachment_id": ticket["attachment"]["id"]},
            "claim_summary": "not from this event",
        },
        headers=as_member,
    )
    assert response.status_code == 422
    assert "BR-E-04" in response.text


def test_an_attachment_citation_needs_a_claim(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    event = an_event(api, as_member)
    ticket = api.post(
        f"/api/v1/events/{event['id']}/attachments",
        json={"filename": "q.pdf", "media_type": "application/pdf"},
        headers=as_member,
    ).json()
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "attachment_locator": {"attachment_id": ticket["attachment"]["id"]},
        },
        headers=as_member,
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- BR-E-06


def test_a_correction_is_a_new_citation(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The old row is retained and points forward. "We used to believe this" stays answerable."""
    event = an_event(api, as_member)
    original = cite(api, as_member, event, "revised quote").json()

    replacement = api.post(
        f"/api/v1/evidence/{original['id']}/supersede",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": original["target_id"],
            "assertion": "supports",
            "text_locator": {
                "char_start": SAID.index("by Friday"),
                "char_end": SAID.index("by Friday") + len("by Friday"),
            },
            "excerpt": "by Friday",
            "confidence": 95,
        },
        headers=as_member,
    )
    assert replacement.status_code == 201, replacement.text
    new_id = replacement.json()["id"]

    old = api.get(f"/api/v1/evidence/{original['id']}", headers=as_member).json()
    assert old["superseded_by_id"] == new_id
    assert old["excerpt"] == "revised quote", "the original citation is never edited"

    listed = api.get(
        f"/api/v1/evidence?target_type=work&target_id={original['target_id']}",
        headers=as_member,
    ).json()["items"]
    assert {row["id"] for row in listed} == {original["id"], new_id}


def test_superseding_twice_is_refused(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """Two corrections of one citation would fork the record with no way to say which is current."""
    event = an_event(api, as_member)
    original = cite(api, as_member, event, "revised quote").json()
    body = {
        "event_id": event["id"],
        "target_type": "work",
        "target_id": original["target_id"],
        "assertion": "supports",
        "text_locator": {"char_start": 0, "char_end": 3},
        "excerpt": "Nam",
    }
    first = api.post(
        f"/api/v1/evidence/{original['id']}/supersede", json=body, headers=as_member
    )
    assert first.status_code == 201
    second = api.post(
        f"/api/v1/evidence/{original['id']}/supersede", json=body, headers=as_member
    )
    assert second.status_code == 422
    assert "BR-E-06" in second.text


def test_a_member_cannot_correct_somebody_elses_citation(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Reading an Event is not standing to rewrite somebody else's reading of it."""
    event = an_event(api, as_member)
    original = cite(api, as_member, event, "revised quote").json()
    other = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    response = api.post(
        f"/api/v1/evidence/{original['id']}/supersede",
        json={
            "event_id": event["id"],
            "target_type": "work",
            "target_id": original["target_id"],
            "assertion": "supports",
            "text_locator": {"char_start": 0, "char_end": 3},
            "excerpt": "Nam",
        },
        headers=other,
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- authorization


def test_a_viewer_cannot_cite(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    event = an_event(api, as_member)
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "viewer")
    viewer = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    assert cite(api, viewer, event, "revised quote").status_code == 403


def test_a_restricted_event_cannot_be_quoted_into_visibility(
    api: TestClient, realm: Realm, as_member: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-E-08 reaches Evidence too.

    Somebody who was never party to a restricted conversation must not be able to lift a line out
    of it into a citation everybody can read.
    """
    event = api.post(
        "/api/v1/events",
        json={
            "type": "MANUAL_CAPTURE",
            "occurred_at": NOW.isoformat(),
            "body_text": SAID,
            "sensitivity": "restricted",
            "participants": [{"role": "speaker", "person_id": str(work_org.member)}],
        },
        headers=as_member,
    ).json()
    outsider = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    response = cite(api, outsider, event, "revised quote")
    assert response.status_code == 404, "the event is not readable, so it is not citable"


def test_another_organizations_event_cannot_be_cited(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    response = api.post(
        "/api/v1/evidence",
        json={
            "event_id": str(uuid.uuid4()),
            "target_type": "work",
            "target_id": str(uuid.uuid4()),
            "assertion": "supports",
            "text_locator": {"char_start": 0, "char_end": 3},
            "excerpt": "Nam",
        },
        headers=as_member,
    )
    assert response.status_code == 404
