"""External identity resolution at capture (PQ-7, ADR-0054, BR-I-06).

A channel message arrives carrying a phone number and nothing else. Whether that number becomes a
Person decides whether the rest of the system can attribute a promise to anybody — and getting it
wrong records a commitment against a colleague who never made one, which is worse than failing to
notice the promise at all.

So the assertions here are mostly negative. The resolving case is one test; the six ways a mapping
is *not* good enough are the design. `may_attribute` requires a human confirmation **and** a strong
score, and this file exists to prove each half is load-bearing.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.identity.domain import MIN_ATTRIBUTION_CONFIDENCE
from app.platform.ids import uuid7
from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
CHANNEL = "openclaw.whatsapp"
PROMISE = "Thanks for the call. I will send the revised quote on Friday."


def a_handle() -> str:
    """A number no other test has used. The mapping table is unique organization-wide."""
    return f"+8490{uuid.uuid4().int % 10**7:07d}"


def mapping(
    api: TestClient,
    headers: dict[str, str],
    person_id: uuid.UUID,
    handle: str,
    *,
    confidence: int,
    confirm: bool,
    source_system: str = CHANNEL,
) -> dict:
    created = api.post(
        f"/api/v1/people/{person_id}/external-identities",
        json={
            "source_system": source_system,
            "external_id": handle,
            "confidence": confidence,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["confirmed_at"] is None, "ADR-0037: mappings are created unconfirmed"
    if not confirm:
        return row
    confirmed = api.post(
        f"/api/v1/external-identities/{row['id']}/confirm",
        headers={**headers, "If-Match": f'W/"{row["version"]}"'},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def capture(
    api: TestClient,
    headers: dict[str, str],
    *,
    participants: list[dict],
    source_system: str = CHANNEL,
) -> dict:
    response = api.post(
        "/api/v1/events",
        json={
            "type": "EXTERNAL_MESSAGE",
            "occurred_at": NOW.isoformat(),
            "body_text": PROMISE,
            "source_system": source_system,
            "source_ref": f"m-{uuid.uuid4().hex[:10]}",
            "participants": participants,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    detail = api.get(f"/api/v1/events/{response.json()['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    return detail.json()


def speaker(handle: str) -> dict:
    return {"role": "speaker", "external_handle": handle}


def only_participant(event: dict) -> dict:
    assert len(event["participants"]) == 1
    return event["participants"][0]


# --------------------------------------------------------------------------- resolving


def test_a_confirmed_and_strong_mapping_resolves(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The one positive case, and the one that makes the commitment vertical reachable at all."""
    handle = a_handle()
    mapping(api, as_admin, work_org.member, handle, confidence=95, confirm=True)

    participant = only_participant(capture(api, as_admin, participants=[speaker(handle)]))

    assert participant["person_id"] == str(work_org.member)
    assert participant["match_confidence"] == 95, (
        "the mapping's own confidence, not a number invented at capture"
    )
    assert participant["resolved_at"] is not None
    assert participant["external_handle"] == handle, "the raw identifier is never discarded"


def test_the_threshold_is_exactly_the_rule_identity_publishes(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-I-06 has one definition. A second one here would be free to drift from it."""
    handle = a_handle()
    mapping(
        api,
        as_admin,
        work_org.member,
        handle,
        confidence=MIN_ATTRIBUTION_CONFIDENCE,
        confirm=True,
    )
    participant = only_participant(capture(api, as_admin, participants=[speaker(handle)]))
    assert participant["person_id"] == str(work_org.member)


# --------------------------------------------------------------------------- refusing


def test_an_unconfirmed_mapping_does_not_resolve(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """Confidence alone is a number somebody typed. Nobody has vouched for this handle."""
    handle = a_handle()
    mapping(api, as_admin, work_org.member, handle, confidence=100, confirm=False)

    participant = only_participant(capture(api, as_admin, participants=[speaker(handle)]))

    assert participant["person_id"] is None
    assert participant["match_confidence"] == 0
    assert participant["resolved_at"] is None
    assert participant["external_handle"] == handle


def test_a_confirmed_but_weak_mapping_does_not_resolve(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """A human looked, and was not sure. Being looked at is not the same as being right."""
    handle = a_handle()
    mapping(
        api,
        as_admin,
        work_org.member,
        handle,
        confidence=MIN_ATTRIBUTION_CONFIDENCE - 1,
        confirm=True,
    )

    participant = only_participant(capture(api, as_admin, participants=[speaker(handle)]))

    assert participant["person_id"] is None
    assert participant["external_handle"] == handle


def test_an_unknown_handle_is_preserved_and_resolves_to_nobody(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """Lookup-only: an unseen number creates nothing, least of all a mapping vouching for itself."""
    handle = a_handle()
    participant = only_participant(capture(api, as_admin, participants=[speaker(handle)]))

    assert participant["person_id"] is None
    assert participant["external_handle"] == handle


def test_a_mapping_from_another_channel_does_not_resolve(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """`(source_system, external_id)` is the key. The same digits in Slack are somebody else."""
    handle = a_handle()
    mapping(
        api,
        as_admin,
        work_org.member,
        handle,
        confidence=100,
        confirm=True,
        source_system="slack",
    )

    participant = only_participant(capture(api, as_admin, participants=[speaker(handle)]))

    assert participant["person_id"] is None


def test_a_confirmed_mapping_in_another_organization_cannot_resolve(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    app_session_factory: sessionmaker[Session],
) -> None:
    """Tenancy, on the newest path in the system.

    The rival mapping is written through its own session under its own org context, because a
    fixture built any other way would prove nothing about what this organization can see.
    """
    handle = a_handle()
    rival_org = uuid7()
    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(rival_org)}
    )
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Rival', :slug)"),
        {"id": rival_org, "slug": f"rival-{uuid.uuid4().hex[:12]}"},
    )
    rival_person = uuid7()
    session.execute(
        text(
            "INSERT INTO person (id, org_id, display_name, status) "
            "VALUES (:id, :org, 'Rival Person', 'active')"
        ),
        {"id": rival_person, "org": rival_org},
    )
    session.execute(
        text(
            "INSERT INTO external_identity "
            "(id, org_id, person_id, source_system, external_id, confidence, "
            " confirmed_by_person_id, confirmed_at) "
            "VALUES (:id, :org, :person, :system, :handle, 100, :person, now())"
        ),
        {
            "id": uuid7(),
            "org": rival_org,
            "person": rival_person,
            "system": CHANNEL,
            "handle": handle,
        },
    )
    session.commit()
    session.close()

    participant = only_participant(capture(api, as_admin, participants=[speaker(handle)]))

    assert participant["person_id"] is None, "a mapping leaked across a tenant boundary"
    assert participant["person_id"] != str(rival_person)


# --------------------------------------------------------------------------- what it never does


def test_a_person_the_caller_named_is_never_overruled(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """A human naming a colleague is an assertion capture has no business re-deriving.

    The mapping here points at somebody else entirely. Resolution must leave the caller's claim
    alone rather than quietly disagree with the person who filed the Event.
    """
    handle = a_handle()
    mapping(api, as_admin, work_org.outsider, handle, confidence=100, confirm=True)

    participant = only_participant(
        capture(
            api,
            as_admin,
            participants=[
                {
                    "role": "speaker",
                    "external_handle": handle,
                    "person_id": str(work_org.member),
                    "confidence": 50,
                }
            ],
        )
    )

    assert participant["person_id"] == str(work_org.member)
    assert participant["match_confidence"] == 50


def test_resolution_creates_no_identity_and_no_person(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """ADR-0054 is lookup-only. A resolver that can write is one that can vouch for itself."""

    def counts() -> tuple[int, int]:
        scoped_session.rollback()
        return (
            scoped_session.execute(
                text("SELECT count(*) FROM external_identity WHERE org_id = :org"),
                {"org": work_org.org_id},
            ).scalar_one(),
            scoped_session.execute(
                text("SELECT count(*) FROM person WHERE org_id = :org"),
                {"org": work_org.org_id},
            ).scalar_one(),
        )

    before = counts()
    capture(api, as_admin, participants=[speaker(a_handle())])
    assert counts() == before


def test_every_participant_on_one_event_is_resolved_independently(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """A thread has a known sender and unknown numbers in it, and both must come out right."""
    known, unknown = a_handle(), a_handle()
    mapping(api, as_admin, work_org.member, known, confidence=99, confirm=True)

    event = capture(
        api,
        as_admin,
        participants=[
            speaker(known),
            {"role": "recipient", "external_handle": unknown},
        ],
    )
    by_handle = {row["external_handle"]: row for row in event["participants"]}

    assert by_handle[known]["person_id"] == str(work_org.member)
    assert by_handle[unknown]["person_id"] is None
