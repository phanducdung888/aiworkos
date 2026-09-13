"""An email becomes a Commitment somebody approved (CP20).

The whole point of the checkpoint, end to end, with the message normalised by the **connector's own
code** rather than by a test that re-implements it. If the connector and the capture API ever
disagree about what a message looks like, this is where it shows.

    RFC 5322 bytes → connector → POST /events → Event → participants →
    identity resolution → AI analysis → Evidence → Proposal → approval → execution → Commitment

Each step is performed by whoever is entitled to it. The connector holds one grant in the whole
authorization matrix (ADR-0060) and uses it once; a person approves; a worker executes.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from connectors.imap.canonical import CanonicalMessage, parse_message
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.conftest import (
    Realm,
    WorkOrg,
    auth,
    execute_approval,
    grant,
    subject_of,
)

pytestmark = pytest.mark.integration

SENDER = "mai.tran@example.test"
RECIPIENT = "khoa.phung@example.test"


def an_email(
    *,
    message_id: str | None = None,
    body: str = "Thanks for the call. I will send the revised quote by Friday.",
    date: str = "Sat, 12 Sep 2026 09:00:00 +0000",
    sender: str = SENDER,
) -> bytes:
    reference = message_id or f"{uuid.uuid4().hex}@example.test"
    return (
        f"From: Mai Tran <{sender}>\r\n"
        f"To: Khoa Phung <{RECIPIENT}>\r\n"
        "Subject: Revised quote\r\n"
        f"Message-ID: <{reference}>\r\n"
        f"Date: {date}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body}\r\n"
    ).encode()


def deliver(api: TestClient, headers: dict[str, str], message: CanonicalMessage):
    """Exactly what `IngestionClient` sends: the canonical body and the derived key."""
    return api.post(
        "/api/v1/events",
        json=message.as_event(),
        headers={**headers, "Idempotency-Key": message.idempotency_key},
    )


@pytest.fixture
def as_connector(
    realm: Realm, work_org: WorkOrg, scoped_session: Session
) -> dict[str, str]:
    """A credential holding the ingestion role and nothing else (ADR-0060, migration 0014).

    A Person row, because that is what this system authenticates — a Keycloak subject resolved to a
    Person and their roles (ADR-0031). A connector is not a human, but a second authentication path
    would be a second way into the application and a second thing to get right.

    `dept_lead` is used because the `roles` fixture grants it nothing, so this credential holds
    exactly one role and the tests below are about that role rather than about a subtraction.
    """
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "ingestion")
    scoped_session.commit()
    return auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)


@pytest.fixture
def sender_is_known(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """A confirmed mapping from the sender's address to a Person (ADR-0054, BR-I-06).

    `source_system` matches what the connector sends, and the address is lowercased the same way —
    a mapping filed differently would simply never match, which is the failure worth noticing here.
    """
    created = api.post(
        f"/api/v1/people/{work_org.member}/external-identities",
        json={"source_system": "email.imap", "external_id": SENDER, "confidence": 95},
        headers=as_admin,
    ).json()
    confirmed = api.post(
        f"/api/v1/external-identities/{created['id']}/confirm",
        headers={**as_admin, "If-Match": f'W/"{created["version"]}"'},
    )
    assert confirmed.status_code == 200, confirmed.text


# --------------------------------------------------------------------------- the journey


def test_an_email_becomes_a_commitment_a_person_approved(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    sender_is_known: None,
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    raw = an_email()
    message = parse_message(raw)

    # 1. The connector delivers. One grant, used once.
    delivered = deliver(api, as_connector, message)
    assert delivered.status_code == 201, delivered.text
    event = delivered.json()
    assert event["source_system"] == "email.imap"
    assert event["source_ref"] == message.source_ref
    assert event["occurred_at"].startswith("2026-09-12T09:00:00")

    # 2. WorkOS resolved the sender. The connector asserted nothing about who anybody is.
    detail = api.get(f"/api/v1/events/{event['id']}", headers=as_admin).json()
    by_handle = {row["external_handle"]: row for row in detail["participants"]}
    assert by_handle[SENDER]["person_id"] == str(work_org.member)
    assert by_handle[RECIPIENT]["person_id"] is None, "an unmapped address resolves to nobody"

    # 3. A person asks for the analysis; delivery did not.
    analysis = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin).json()
    proposal = api.get(
        f"/api/v1/proposals/{analysis['proposal_ids'][0]}", headers=as_admin
    ).json()
    assert proposal["target_type"] == "commitment"
    assert proposal["action"]["arguments"]["committed_by_person_id"] == str(work_org.member)
    # ADR-0055: the deadline is read from the message, against the message's own Date.
    assert proposal["action"]["arguments"]["due_date"] == "2026-09-18"

    # 4. The citation is the email's own words (BR-E-05).
    evidence = api.get(
        f"/api/v1/evidence/{proposal['evidence_ids'][0]}", headers=as_admin
    ).json()
    assert evidence["excerpt"] in message.body_text

    # 5. Nothing exists until a person says so.
    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM commitment WHERE org_id = :org"),
        {"org": work_org.org_id},
    ).scalar_one() == 0

    record = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**as_admin, "If-Match": f'W/"{proposal["version"]}"'},
    ).json()
    outcome = execute_approval(api, as_admin, record["id"], worker_session_factory)
    assert outcome.execution_status == "executed", outcome.body

    commitment = api.get(f"/api/v1/commitments/{outcome.entity_id}", headers=as_admin).json()
    assert commitment["committed_by_person_id"] == str(work_org.member)
    assert commitment["due_date"] == "2026-09-18"
    assert commitment["origin_event_id"] == event["id"]

    # 6. And it is walkable back to the email (ADR-0056).
    found = api.get(
        "/api/v1/proposals",
        params={"resulting_entity_id": commitment["id"]},
        headers=as_admin,
    ).json()
    assert [row["id"] for row in found["items"]] == analysis["proposal_ids"]


def test_a_sender_nobody_vouched_for_produces_no_commitment(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The conservative half, from a real message. No confirmed mapping, so no attribution.

    BR-AI-34: an unresolved speaker is not a committer, and the promise is refused rather than
    attributed to a guess — an address is not a person until somebody says it is.
    """
    message = parse_message(an_email(sender="stranger@example.test"))
    event = deliver(api, as_connector, message).json()

    analysis = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin).json()
    assert analysis["proposal_ids"] == []


# --------------------------------------------------------------------------- redelivery


def test_a_retried_delivery_creates_one_event(
    api: TestClient,
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """What a connector author actually worries about: does my retry cost anybody a second promise?

    It cannot, because the retry produces no second Event and there is nothing else to analyse.
    """
    message = parse_message(an_email())
    first = deliver(api, as_connector, message)
    second = deliver(api, as_connector, message)
    third = deliver(api, as_connector, message)

    assert first.status_code == 201
    assert first.json()["id"] == second.json()["id"] == third.json()["id"]

    scoped_session.rollback()
    assert scoped_session.execute(
        text(
            "SELECT count(*) FROM event WHERE org_id = :org AND source_ref = :ref "
            "AND revision_of_event_id IS NULL"
        ),
        {"org": work_org.org_id, "ref": message.source_ref},
    ).scalar_one() == 1


def test_a_redelivery_without_the_header_creates_no_second_event(
    api: TestClient,
    as_connector: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Why the header stays optional (ADR-0060 §4), and what CP23 changed about it.

    Correctness never depended on it: BR-E-02 recognises the redelivery and no second Event is
    written. What a connector used to lose was a *usable answer* — telling a caller "you already
    sent this, here it is" means returning the Event, and the ingestion role could not read one, so
    the redelivery came back 404.

    CP23's `PERSONAL` read removed that. A connector may read back what it delivered, so the
    redelivery now answers with the original Event whether or not the key was sent. The derived key
    is still worth sending — it replays without touching the row at all — but the fallback is no
    longer a confusing refusal.
    """
    message = parse_message(an_email())
    first = api.post("/api/v1/events", json=message.as_event(), headers=as_connector)
    second = api.post("/api/v1/events", json=message.as_event(), headers=as_connector)

    assert first.status_code == 201
    assert second.status_code in (200, 201), second.text
    assert second.json()["id"] == first.json()["id"]

    scoped_session.rollback()
    assert scoped_session.execute(
        text(
            "SELECT count(*) FROM event WHERE org_id = :org AND source_ref = :ref "
            "AND revision_of_event_id IS NULL"
        ),
        {"org": work_org.org_id, "ref": message.source_ref},
    ).scalar_one() == 1


def test_a_corrected_email_under_the_same_id_is_a_revision(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """A correction carries the original's `Message-ID` and is a revision, not a retry (BR-E-02).

    This is the case that made the key cover the body: derived from the reference alone, the guard
    would have refused the correction as the same key with a different body.
    """
    reference = f"{uuid.uuid4().hex}@example.test"
    original = parse_message(an_email(message_id=reference))
    corrected = parse_message(
        an_email(message_id=reference, body="Actually, I will send it on Monday.")
    )
    assert original.source_ref == corrected.source_ref
    assert original.idempotency_key != corrected.idempotency_key

    first = deliver(api, as_connector, original).json()
    second = deliver(api, as_connector, corrected)

    assert second.status_code == 201, second.text
    assert second.json()["revision_of_event_id"] == first["id"]


def test_two_emails_are_two_events(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    first = deliver(api, as_connector, parse_message(an_email())).json()
    second = deliver(api, as_connector, parse_message(an_email())).json()
    assert first["id"] != second["id"]


def test_a_message_with_no_date_is_never_delivered_with_an_invented_one(
    api: TestClient, as_connector: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The connector refuses at the boundary rather than substituting `now()`.

    CP15 reads deadlines against `occurred_at`, so a timestamp this code made up would become a
    real date on somebody's promise.
    """
    from connectors.imap.canonical import Unnormalisable

    raw = (
        f"From: Mai <{SENDER}>\r\nTo: <{RECIPIENT}>\r\n"
        "Message-ID: <nodate@example.test>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\nI will send it by Friday.\r\n"
    ).encode()
    with pytest.raises(Unnormalisable):
        parse_message(raw)

    # And with the server's own receipt time it is deliverable, because that is a fact.
    received = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
    delivered = deliver(api, as_connector, parse_message(raw, received_at=received))
    assert delivered.status_code == 201
    assert delivered.json()["occurred_at"].startswith("2026-09-12T09:00:00")


# --------------------------------------------------------------------------- attachments


def deliver_with_attachments(
    api: TestClient, headers: dict[str, str], message: CanonicalMessage
) -> dict:
    """Delivery the way `IngestionClient` does it: the Event, then each file against it.

    Written out here rather than calling the connector's client because that client speaks real
    HTTP to a URL, and the API under test is a `TestClient`. What must match is the *sequence* —
    reserve, PUT to the presigned URL, complete — and that is what this reproduces.
    """
    event = deliver(api, headers, message).json()
    for attachment in message.attachments:
        ticket = api.post(
            f"/api/v1/events/{event['id']}/attachments",
            json={
                "filename": attachment.filename,
                "media_type": attachment.media_type,
            },
            headers={
                **headers,
                "Idempotency-Key": (
                    f"{event['id']}:{attachment.filename}:{attachment.size_bytes}"
                ),
            },
        )
        assert ticket.status_code == 201, ticket.text
        reserved = ticket.json()
        api.app.state.object_store.write_through(  # type: ignore[attr-defined]
            reserved["upload_url"], attachment.content, attachment.media_type
        )
        completed = api.post(
            f"/api/v1/events/{event['id']}/attachments/"
            f"{reserved['attachment']['id']}/complete",
            headers=headers,
        )
        assert completed.status_code == 200, completed.text
    return event


def an_email_with_a_quote(*, body: str | None = None) -> bytes:
    import base64

    pdf = base64.b64encode(b"%PDF-1.4 the revised quote, in full").decode()
    text = body or "Thanks for the call. I will send the revised quote by Friday."
    return (
        f"From: Mai Tran <{SENDER}>\r\n"
        f"To: Khoa Phung <{RECIPIENT}>\r\n"
        "Subject: Revised quote\r\n"
        f"Message-ID: <{uuid.uuid4().hex}@example.test>\r\n"
        "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
        'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
        f"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{text}\r\n"
        "--b\r\nContent-Type: application/pdf\r\n"
        'Content-Disposition: attachment; filename="quote.pdf"\r\n'
        "Content-Transfer-Encoding: base64\r\n\r\n"
        f"{pdf}\r\n"
        "--b--\r\n"
    ).encode()


def test_an_email_attachment_is_delivered_against_its_event(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    sender_is_known: None,
    roles: None,
    work_org: WorkOrg,
) -> None:
    """CP23's point: the file arrives, and it arrives attached to the message it came with."""
    message = parse_message(an_email_with_a_quote())
    assert len(message.attachments) == 1

    event = deliver_with_attachments(api, as_connector, message)

    listed = api.get(f"/api/v1/events/{event['id']}", headers=as_admin).json()
    assert len(listed["attachments"]) == 1
    attached = listed["attachments"][0]
    assert attached["filename"] == "quote.pdf"
    assert attached["media_type"] == "application/pdf"
    assert attached["status"] == "available"
    # Size and checksum are read from the store, never believed from the client (ADR-0039).
    assert attached["size_bytes"] == message.attachments[0].size_bytes


def test_the_attachment_does_not_change_what_the_ai_reads(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    sender_is_known: None,
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
) -> None:
    """The body stays the words somebody wrote.

    A connector that mentioned its attachments in `body_text` would put text into a record nobody
    sent, and Evidence is quoted from that record (BR-E-05). The file is reachable *beside* the
    Event, which is what ADR-0039 designed.
    """
    message = parse_message(an_email_with_a_quote())
    event = deliver_with_attachments(api, as_connector, message)

    stored = api.get(f"/api/v1/events/{event['id']}", headers=as_admin).json()
    assert "quote.pdf" not in (stored["body_text"] or "")
    assert "%PDF" not in (stored["body_text"] or "")

    analysis = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin).json()
    evidence = api.get(
        f"/api/v1/evidence/{analysis['evidence_ids'][0]}", headers=as_admin
    ).json()
    assert evidence["excerpt"] in message.body_text


def test_the_whole_journey_carries_the_attachment(
    api: TestClient,
    as_admin: dict[str, str],
    as_connector: dict[str, str],
    sender_is_known: None,
    roles: None,
    agent_enabled: None,
    work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """IMAP → Event + attachment → analysis → Evidence → Proposal → approval → Commitment.

    The attachment rides along rather than participating: the AI reads the covering note, a person
    approves what it proposed, and the file stays reachable from the Event the promise came from.
    """
    message = parse_message(an_email_with_a_quote())
    event = deliver_with_attachments(api, as_connector, message)

    analysis = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin).json()
    proposal = api.get(
        f"/api/v1/proposals/{analysis['proposal_ids'][0]}", headers=as_admin
    ).json()
    assert proposal["target_type"] == "commitment"

    record = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**as_admin, "If-Match": f'W/"{proposal["version"]}"'},
    ).json()
    outcome = execute_approval(api, as_admin, record["id"], worker_session_factory)
    assert outcome.execution_status == "executed", outcome.body

    commitment = api.get(f"/api/v1/commitments/{outcome.entity_id}", headers=as_admin).json()
    assert commitment["origin_event_id"] == event["id"]

    # And from the promise, back to the file that came with it.
    source = api.get(f"/api/v1/events/{commitment['origin_event_id']}", headers=as_admin).json()
    assert [row["filename"] for row in source["attachments"]] == ["quote.pdf"]


def test_redelivering_a_message_does_not_duplicate_its_attachment(
    api: TestClient,
    as_connector: dict[str, str],
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
) -> None:
    """A retried pass reserves the same attachment rather than a second copy of the same file.

    The key is derived from the Event, the filename and the size, so the second reservation replays
    the first answer instead of creating a row.
    """
    message = parse_message(an_email_with_a_quote())
    first = deliver_with_attachments(api, as_connector, message)
    second = deliver_with_attachments(api, as_connector, message)

    assert first["id"] == second["id"]
    stored = api.get(f"/api/v1/events/{first['id']}", headers=as_admin).json()
    assert len(stored["attachments"]) == 1
