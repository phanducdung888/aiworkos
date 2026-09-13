"""What a real mailbox broke that a constructed one never did (CP24).

Every test here is a defect the pilot found. None of them needed a mail server to find — they
needed a message somebody would actually send: one with a Vietnamese filename, one with a file
bigger than the ceiling, one bigger than the connector.
"""

from __future__ import annotations

import os
from email.message import EmailMessage

import pytest

from connectors.imap.canonical import (
    MAX_ATTACHMENT_BYTES,
    MAX_MESSAGE_BYTES,
    Attachment,
    TooLarge,
    parse_message,
)
from connectors.imap.client import IngestionClient, _attachment_key

from .conftest import Capture


def message(*, filename: str | None = None, size: int = 32) -> bytes:
    m = EmailMessage()
    m["Message-ID"] = "<pilot@example.test>"
    m["Subject"] = "pilot"
    m["From"] = "tomas.lead@example.test"
    m["To"] = "pilot@example.test"
    m["Date"] = "Mon, 08 Sep 2026 09:15:00 +0000"
    m.set_content("A body, so the message is normalisable.\n")
    if filename is not None:
        m.add_attachment(
            os.urandom(size), maintype="application", subtype="octet-stream", filename=filename
        )
    return m.as_bytes()


# --------------------------------------------------------------------------- unicode filenames


def test_an_attachment_key_survives_being_put_in_a_header() -> None:
    """A header value is latin-1 on the wire, and a filename is whatever the sender typed.

    The key used to be `f"{event}:{filename}:{size}"`. `kế-hoạch.txt` made `http.client` raise
    `UnicodeEncodeError` *before the request left the process*, which aborted the whole pass and
    left the message unseen to be retried every interval for as long as the service ran.
    """
    key = _attachment_key(
        "01a09997-880f-7e4b-a29b-f6dca007bf76",
        Attachment(filename="kế-hoạch.txt", media_type="text/plain", content=b"x"),
    )
    key.encode("latin-1")  # the assertion: this is what http.client does
    assert key.startswith("sha256:")


def test_the_same_file_still_reserves_the_same_attachment() -> None:
    """The property the key exists for, which hashing must not lose."""
    event = "01a09997-880f-7e4b-a29b-f6dca007bf76"
    one = Attachment(filename="invoice.pdf", media_type="application/pdf", content=b"abc")
    same = Attachment(filename="invoice.pdf", media_type="application/pdf", content=b"xyz")
    other = Attachment(filename="invoice.pdf", media_type="application/pdf", content=b"abcd")
    assert _attachment_key(event, one) == _attachment_key(event, same)
    assert _attachment_key(event, one) != _attachment_key(event, other)
    assert _attachment_key("01a09997-0000-7e4b-a29b-f6dca007bf76", one) != _attachment_key(
        event, one
    )


def test_a_unicode_filename_is_delivered_rather_than_crashing(workos: Capture) -> None:
    parsed = parse_message(message(filename="kế-hoạch.txt"))
    client = IngestionClient(workos.url, token="t", organization_id="org")
    outcome = client.deliver_attachments("event-1", parsed.attachments)
    assert outcome.delivered == 1
    assert outcome.failed == ()
    assert parsed.attachments[0].filename == "kế-hoạch.txt"


# --------------------------------------------------------------------------- limits


def test_an_oversized_file_is_named_rather_than_silently_dropped() -> None:
    """The module said so in a docstring and did not do it.

    An attachment over the ceiling was skipped with no record anywhere, which makes "the file never
    arrived" a question with no answer: the Event is there, the file is not, and nothing between
    the two says why.
    """
    parsed = parse_message(message(filename="huge.bin", size=MAX_ATTACHMENT_BYTES + 1))
    assert parsed.attachments == ()
    assert [name for name, _size in parsed.oversized_attachments] == ["huge.bin"]
    assert parsed.oversized_attachments[0][1] == MAX_ATTACHMENT_BYTES + 1


def test_a_file_at_the_ceiling_is_carried() -> None:
    """Exactly at the limit is inside it; the check is `>`, and a boundary nobody tests moves."""
    parsed = parse_message(message(filename="just.bin", size=MAX_ATTACHMENT_BYTES))
    assert [a.filename for a in parsed.attachments] == ["just.bin"]
    assert parsed.oversized_attachments == ()


def test_a_message_past_the_wire_ceiling_is_refused_before_it_is_parsed() -> None:
    """The per-file limit bounds nothing on its own.

    Measured in CP24: ten files of 10 MB, every one of them inside `MAX_ATTACHMENT_BYTES`, peaked
    at 1.13 GB resident. Parsing is where the amplification happens — roughly nine times the wire
    size once the payload is decoded — so the check has to come first.
    """
    raw = b"Message-ID: <big@example.test>\r\n\r\n" + b"x" * MAX_MESSAGE_BYTES
    with pytest.raises(TooLarge):
        parse_message(raw)


def test_the_wire_ceiling_is_what_actually_bounds_a_multi_file_message() -> None:
    m = EmailMessage()
    m["Message-ID"] = "<many@example.test>"
    m["From"] = "a@example.test"
    m["To"] = "b@example.test"
    m["Date"] = "Mon, 08 Sep 2026 09:15:00 +0000"
    m.set_content("body\n")
    for index in range(5):
        m.add_attachment(
            b"\0" * (9 * 1024 * 1024),
            maintype="application",
            subtype="octet-stream",
            filename=f"file-{index}.bin",
        )
    with pytest.raises(TooLarge):
        parse_message(m.as_bytes())


# --------------------------------------------------------------------------- failure containment


def test_one_impossible_file_does_not_abort_the_others(workos: Capture) -> None:
    """`deliver_attachments` promises the rest continue. It promised it with three `except` classes.

    The `UnicodeEncodeError` was none of them, so the promise held only for failures somebody had
    already thought of — which is the opposite of what a containment boundary is for.
    """

    good = Attachment(filename="fine.txt", media_type="text/plain", content=b"ok")

    class Broken:
        filename = "broken.txt"
        media_type = "text/plain"
        content = b"x"

        @property
        def size_bytes(self) -> int:
            raise RuntimeError("nothing anticipated this")

    client = IngestionClient(workos.url, token="t", organization_id="org")
    outcome = client.deliver_attachments("event-1", (Broken(), good))  # type: ignore[arg-type]
    assert outcome.delivered == 1
    assert outcome.failed == ("broken.txt",)


# --------------------------------------------------------------------------- credentials


def test_a_rejected_credential_leaves_the_message_in_the_mailbox(workos: Capture) -> None:
    """A 401 is about the connector, not about the message.

    The pilot's token reached its lifespan mid-run. The connector read 401 as "this message is
    wrong", marked it seen and moved on — the message was consumed from the mailbox and had never
    reached WorkOS. `DeliveryUnavailable` is what stops the pass and leaves the mail where it is.
    """
    from connectors.imap.client import DeliveryRefused, DeliveryUnavailable

    workos.status = 401
    parsed = parse_message(message())
    client = IngestionClient(workos.url, token="expired", organization_id="org", attempts=1)
    with pytest.raises(DeliveryUnavailable):
        client.deliver(parsed)
    assert not isinstance(DeliveryUnavailable("x"), DeliveryRefused)


def test_a_rejected_credential_does_not_discard_an_attachment_either(workos: Capture) -> None:
    workos.attachment_status = 403
    parsed = parse_message(message(filename="invoice.pdf"))
    client = IngestionClient(workos.url, token="expired", organization_id="org", attempts=1)
    outcome = client.deliver_attachments("event-1", parsed.attachments)
    # Reported as failed rather than raising out of the loop, which is `deliver_attachments`'
    # contract — but the *reason* recorded is unavailability, so a retry is the right next move.
    assert outcome.delivered == 0
    assert outcome.failed == ("invoice.pdf",)


def test_a_genuinely_bad_message_is_still_refused(workos: Capture) -> None:
    """The distinction has to cut both ways, or every 4xx becomes an infinite retry."""
    from connectors.imap.client import DeliveryRefused

    workos.status = 422
    parsed = parse_message(message())
    client = IngestionClient(workos.url, token="t", organization_id="org", attempts=1)
    with pytest.raises(DeliveryRefused):
        client.deliver(parsed)
