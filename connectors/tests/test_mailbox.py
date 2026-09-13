"""The IMAP half, against a real mail server. Off unless you turn it on.

    make mail
    RUN_IMAP_TESTS=1 pytest connectors/tests -m imap

Same shape and same reasoning as the repository's real-provider smoke tests. `test_canonical` and
`test_client` cover the halves that need nothing; this covers the one that cannot be covered any
other way — the `imaplib` conversation and the loop around it, which was the only part of the
connector with no test at all when CP20 shipped.

**A test server is not a real mailbox.** GreenMail is an independent implementation of RFC 3501, so
it catches assumptions baked into the client that a fake written alongside it would have shared. It
does not catch what a particular provider does with folder names, flags or fetch responses. That
remains open, and is answered by pointing this at a real mailbox rather than by another test.
"""

from __future__ import annotations

import datetime as dt
import email.message
import imaplib
import os
import smtplib
import uuid

import pytest

from connectors.imap.run import Settings, run_once
from connectors.tests.conftest import Capture

pytestmark = pytest.mark.imap

HOST = os.environ.get("IMAP_TEST_HOST", "127.0.0.1")
SMTP_PORT = int(os.environ.get("GREENMAIL_SMTP_PORT", "3025"))
IMAP_PORT = int(os.environ.get("GREENMAIL_IMAP_PORT", "3143"))


def a_mailbox() -> str:
    """A fresh address per test. GreenMail creates the mailbox on first delivery."""
    return f"ingest-{uuid.uuid4().hex[:10]}@example.test"


def send(
    mailbox: str,
    *,
    body: str = "Thanks for the call. I will send the revised quote by Friday.",
    subject: str = "Revised quote",
    sender: str = "Mai Tran <mai.tran@example.test>",
    message_id: str | None = None,
    date: str | None = "Sat, 12 Sep 2026 09:00:00 +0000",
    html: bool = False,
) -> str:
    reference = message_id or f"{uuid.uuid4().hex}@example.test"
    message = email.message.EmailMessage()
    message["From"] = sender
    message["To"] = mailbox
    message["Subject"] = subject
    message["Message-ID"] = f"<{reference}>"
    if date is not None:
        message["Date"] = date
    if html:
        # HTML *only*. A multipart message with a plain part would prove nothing here: the
        # connector prefers `text/plain`, which `test_canonical` already covers.
        message.set_content(f"<p>{body}</p>", subtype="html")
    else:
        message.set_content(body)
    with smtplib.SMTP(HOST, SMTP_PORT) as smtp:
        smtp.send_message(message)
    return reference


def settings_for(mailbox: str, workos: Capture, **over: object) -> Settings:
    fields: dict[str, object] = {
        "host": HOST,
        "port": IMAP_PORT,
        "security": "none",
        "username": mailbox,
        "password": "any-password-greenmail-accepts",
        "workos_url": workos.url,
        "workos_token": "not-a-real-token",
        "organization_id": "11111111-1111-4111-8111-111111111111",
    }
    fields.update(over)
    return Settings(**fields)  # type: ignore[arg-type]


def unseen(mailbox: str) -> int:
    mail = imaplib.IMAP4(HOST, IMAP_PORT)
    try:
        mail.login(mailbox, "any")
        mail.select("INBOX")
        _status, data = mail.search(None, "UNSEEN")
        return len(data[0].split())
    finally:
        mail.logout()


# --------------------------------------------------------------------------- the pass


def test_a_message_in_a_mailbox_reaches_workos(workos: Capture) -> None:
    """The whole untested path: connect, search, fetch, normalise, deliver, mark seen."""
    mailbox = a_mailbox()
    reference = send(mailbox)

    report = run_once(settings_for(mailbox, workos))

    assert (report.delivered, report.skipped, report.refused) == (1, 0, 0)
    assert len(workos.received) == 1
    delivered = workos.received[0]
    assert delivered["path"] == "/api/v1/events"
    assert delivered["event"]["source_ref"] == reference
    assert delivered["event"]["source_system"] == "email.imap"
    assert delivered["event"]["occurred_at"] == "2026-09-12T09:00:00+00:00"
    assert delivered["event"]["title"] == "Revised quote"
    assert "revised quote by Friday" in delivered["event"]["body_text"]


def test_the_sender_and_the_mailbox_both_become_participants(workos: Capture) -> None:
    mailbox = a_mailbox()
    send(mailbox)

    run_once(settings_for(mailbox, workos))

    participants = workos.received[0]["event"]["participants"]
    assert participants[0] == {
        "role": "speaker",
        "external_handle": "mai.tran@example.test",
    }
    assert {"role": "recipient", "external_handle": mailbox} in participants


def test_the_credential_travels_and_is_never_in_the_body(workos: Capture) -> None:
    mailbox = a_mailbox()
    send(mailbox)

    run_once(settings_for(mailbox, workos))

    delivered = workos.received[0]
    assert delivered["authorization"] == "Bearer not-a-real-token"
    assert delivered["organization"].startswith("11111111")
    assert "not-a-real-token" not in str(delivered["event"])


# --------------------------------------------------------------------------- not twice


def test_a_delivered_message_is_marked_seen_and_not_sent_again(workos: Capture) -> None:
    """The flag is the connector's only memory. Without it every pass re-delivers the mailbox."""
    mailbox = a_mailbox()
    send(mailbox)

    first = run_once(settings_for(mailbox, workos))
    second = run_once(settings_for(mailbox, workos))

    assert first.delivered == 1
    assert second.delivered == 0
    assert len(workos.received) == 1
    assert unseen(mailbox) == 0


def test_a_message_is_left_unseen_when_workos_cannot_be_reached(workos: Capture) -> None:
    """So the next pass tries again. The derived idempotency key is what makes that safe.

    Unseen is the whole point: a connector that marked a message seen before WorkOS had it would
    lose the message, and losing one is the failure mode with no recovery.
    """
    mailbox = a_mailbox()
    send(mailbox)
    url = workos.url
    workos.stop()  # nothing listening

    # Two quick attempts rather than the production five: the behaviour under test is what happens
    # *after* the retries, and waiting out a real backoff would only make the test slow.
    report = run_once(settings_for(mailbox, workos, workos_url=url), attempts=2, backoff=0.0)

    assert report.delivered == 0
    assert unseen(mailbox) == 1, "an undelivered message must still be waiting"


def test_a_refused_message_is_marked_seen_rather_than_retried_forever(
    workos: Capture,
) -> None:
    """A 4xx means the message is wrong, not the moment. Repeating it is how a connector turns its
    own defect into somebody else's outage."""
    mailbox = a_mailbox()
    send(mailbox)
    workos.status = 422

    report = run_once(settings_for(mailbox, workos))

    assert (report.refused, report.delivered) == (1, 0)
    assert unseen(mailbox) == 0


# --------------------------------------------------------------------------- what arrives


def test_several_messages_are_delivered_in_one_pass(workos: Capture) -> None:
    mailbox = a_mailbox()
    references = {send(mailbox) for _ in range(3)}

    report = run_once(settings_for(mailbox, workos))

    assert report.delivered == 3
    assert {row["event"]["source_ref"] for row in workos.received} == references


def test_the_batch_limit_is_respected(workos: Capture) -> None:
    """A mailbox with a backlog is drained over several passes rather than in one enormous one."""
    mailbox = a_mailbox()
    for _ in range(3):
        send(mailbox)

    report = run_once(settings_for(mailbox, workos, batch=2))

    assert report.delivered == 2
    assert unseen(mailbox) == 1


def test_a_message_with_no_date_uses_the_server_receipt_time(workos: Capture) -> None:
    """`_internaldate`, which is the fiddliest parsing in the connector and cannot be exercised
    without a server that supplies an INTERNALDATE."""
    mailbox = a_mailbox()
    before = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    send(mailbox, date=None)

    report = run_once(settings_for(mailbox, workos))

    assert report.delivered == 1
    occurred = dt.datetime.fromisoformat(workos.received[0]["event"]["occurred_at"])
    assert occurred >= before, "the fallback did not use the server's own receipt time"
    assert occurred <= dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)


def test_an_html_message_arrives_as_words(workos: Capture) -> None:
    mailbox = a_mailbox()
    send(mailbox, html=True, body="I will send the revised quote by Friday.")

    run_once(settings_for(mailbox, workos))

    body = workos.received[0]["event"]["body_text"]
    assert "<p>" not in body
    assert "revised quote by Friday" in body


def test_an_empty_mailbox_is_not_an_error(workos: Capture) -> None:
    report = run_once(settings_for(a_mailbox(), workos))
    assert report == type(report)()
    assert workos.received == []
