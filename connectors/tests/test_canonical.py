"""Normalising a message, and the places a connector is tempted to invent something.

The interesting half of a connector is pure, which is why it is a module and not a step inside the
IMAP loop: every case below runs with no mail server, no clock and no network.

The rule under most of these tests is ADR-0058's: **normalisation, not interpretation.** Deciding
which header is the timestamp is normalisation. Deciding that a quoted reply is not part of the
message, or that an address belongs to a particular colleague, is interpretation — and it belongs to
WorkOS, where it can be audited.
"""

from __future__ import annotations

import datetime as dt

import pytest

from connectors.imap.canonical import (
    DEFAULT_SOURCE_SYSTEM,
    Unnormalisable,
    idempotency_key,
    parse_message,
)

RECEIVED = dt.datetime(2026, 9, 13, 8, 0, tzinfo=dt.UTC)


def a_message(
    *,
    headers: str = (
        "From: Mai Tran <Mai@Example.test>\r\n"
        "To: khoa@example.test\r\n"
        "Subject: Revised quote\r\n"
        "Message-ID: <abc123@example.test>\r\n"
        "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
    ),
    content_type: str = "text/plain; charset=utf-8",
    body: str = "Thanks for the call. I will send the revised quote by Friday.",
) -> bytes:
    return f"{headers}Content-Type: {content_type}\r\n\r\n{body}\r\n".encode()


class TestIdentity:
    def test_the_message_id_is_the_source_reference(self) -> None:
        """RFC 5322 defines `Message-ID` to be globally unique, which is what `source_ref` needs."""
        assert parse_message(a_message()).source_ref == "abc123@example.test"

    def test_angle_brackets_are_not_part_of_the_identifier(self) -> None:
        assert "<" not in parse_message(a_message()).source_ref

    def test_a_message_with_no_id_still_gets_a_stable_reference(self) -> None:
        """Non-conformant senders exist. The fallback must be deterministic or a retry duplicates.

        A digest of the bytes: the same message always produces the same reference, so BR-E-02 still
        recognises a redelivery, and two different messages do not collide.
        """
        raw = a_message(
            headers=(
                "From: a@example.test\r\n"
                "To: b@example.test\r\n"
                "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            )
        )
        first, second = parse_message(raw), parse_message(raw)
        assert first.source_ref == second.source_ref
        assert first.source_ref.startswith("sha256:")
        assert parse_message(raw + b"\r\n").source_ref != first.source_ref

    def test_the_source_system_is_the_caller_s_to_name(self) -> None:
        assert parse_message(a_message()).source_system == DEFAULT_SOURCE_SYSTEM
        assert (
            parse_message(a_message(), source_system="email.support").source_system
            == "email.support"
        )


class TestTime:
    def test_the_date_header_is_the_occurrence(self) -> None:
        assert parse_message(a_message()).occurred_at == dt.datetime(
            2026, 9, 12, 9, 0, tzinfo=dt.UTC
        )

    def test_an_offset_is_honoured_rather_than_dropped(self) -> None:
        """09:00 in Hanoi is 02:00 UTC, and CP15 reads deadlines against this (ADR-0055)."""
        raw = a_message(
            headers=(
                "From: a@example.test\r\nTo: b@example.test\r\n"
                "Message-ID: <tz@example.test>\r\n"
                "Date: Sat, 12 Sep 2026 09:00:00 +0700\r\n"
            )
        )
        assert parse_message(raw).occurred_at == dt.datetime(2026, 9, 12, 2, 0, tzinfo=dt.UTC)

    def test_the_server_receipt_time_is_the_fallback(self) -> None:
        """A fact from the transport, not a guess. The distinction is the whole rule."""
        raw = a_message(
            headers=(
                "From: a@example.test\r\nTo: b@example.test\r\n"
                "Message-ID: <nodate@example.test>\r\n"
            )
        )
        assert parse_message(raw, received_at=RECEIVED).occurred_at == RECEIVED

    def test_a_message_with_no_time_at_all_is_refused(self) -> None:
        """It is not delivered with `now()`. A date this code made up would look like one somebody
        sent, and CP15 would read a deadline against it."""
        raw = a_message(
            headers=(
                "From: a@example.test\r\nTo: b@example.test\r\n"
                "Message-ID: <nodate@example.test>\r\n"
            )
        )
        with pytest.raises(Unnormalisable):
            parse_message(raw)


class TestParticipants:
    def test_the_sender_speaks_and_the_rest_receive(self) -> None:
        raw = a_message(
            headers=(
                "From: Mai <mai@example.test>\r\n"
                "To: khoa@example.test, ops@example.test\r\n"
                "Cc: lead@example.test\r\n"
                "Message-ID: <m@example.test>\r\n"
                "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            )
        )
        assert parse_message(raw).participants == (
            {"role": "speaker", "external_handle": "mai@example.test"},
            {"role": "recipient", "external_handle": "khoa@example.test"},
            {"role": "recipient", "external_handle": "ops@example.test"},
            {"role": "recipient", "external_handle": "lead@example.test"},
        )

    def test_a_connector_never_claims_to_know_who_somebody_is(self) -> None:
        """ADR-0058 §2. It has no way to know a `person_id` and no authority to assert one.

        Resolution happens inside WorkOS against confirmed mappings (ADR-0054), and an address
        nobody has vouched for resolves to nobody.
        """
        for participant in parse_message(a_message()).participants:
            assert set(participant) == {"role", "external_handle"}

    def test_addresses_are_lowercased_so_a_mapping_can_match(self) -> None:
        """The same normalisation has to be used when the `external_identity` row is created."""
        assert parse_message(a_message()).participants[0]["external_handle"] == (
            "mai@example.test"
        )

    def test_somebody_addressed_twice_is_one_participant(self) -> None:
        raw = a_message(
            headers=(
                "From: mai@example.test\r\n"
                "To: mai@example.test, khoa@example.test\r\n"
                "Cc: KHOA@example.test\r\n"
                "Message-ID: <dup@example.test>\r\n"
                "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            )
        )
        participants = parse_message(raw).participants
        assert [p["external_handle"] for p in participants] == [
            "mai@example.test",
            "khoa@example.test",
        ]
        # The first role wins: being copied does not stop somebody having spoken.
        assert participants[0]["role"] == "speaker"

    def test_reply_to_is_not_a_participant(self) -> None:
        """It names where an answer should go, not who was present."""
        raw = a_message(
            headers=(
                "From: mai@example.test\r\nTo: khoa@example.test\r\n"
                "Reply-To: noreply@example.test\r\n"
                "Message-ID: <rt@example.test>\r\n"
                "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            )
        )
        handles = [p["external_handle"] for p in parse_message(raw).participants]
        assert "noreply@example.test" not in handles

    def test_a_malformed_address_is_dropped_rather_than_delivered(self) -> None:
        raw = a_message(
            headers=(
                "From: mai@example.test\r\nTo: not-an-address\r\n"
                "Message-ID: <bad@example.test>\r\n"
                "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            )
        )
        handles = [p["external_handle"] for p in parse_message(raw).participants]
        assert handles == ["mai@example.test"]


class TestBody:
    def test_line_endings_are_normalised(self) -> None:
        """The stored body is what Evidence is quoted from and what spans index into (BR-E-05)."""
        assert "\r" not in parse_message(a_message()).body_text

    def test_plain_text_is_preferred_in_a_multipart_message(self) -> None:
        raw = (
            b"From: mai@example.test\r\nTo: khoa@example.test\r\n"
            b"Message-ID: <mp@example.test>\r\n"
            b"Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
            b"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nThe plain one.\r\n"
            b"--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>The HTML one.</p>\r\n"
            b"--b--\r\n"
        )
        assert parse_message(raw).body_text.strip() == "The plain one."

    def test_an_html_only_message_is_stripped_to_its_words(self) -> None:
        """A format change. Dropping the message instead would lose a real conversation."""
        raw = a_message(
            content_type="text/html; charset=utf-8",
            body="<p>I will send the <b>revised quote</b> by Friday.</p>",
        )
        assert parse_message(raw).body_text == "I will send the revised quote by Friday."

    def test_a_quoted_reply_is_delivered_intact(self) -> None:
        """Deciding a quoted reply is not part of the message is interpretation, not normalisation.

        It would also be wrong often enough to matter: people answer inline.
        """
        body = "Friday works.\n\n> On Thursday, Mai wrote:\n> Can we do Friday?"
        assert parse_message(a_message(body=body)).body_text.strip() == body

    def test_a_message_with_no_readable_text_is_refused(self) -> None:
        with pytest.raises(Unnormalisable):
            parse_message(a_message(body="   "))

    def test_an_unknown_charset_does_not_lose_the_message(self) -> None:
        raw = a_message(content_type="text/plain; charset=definitely-not-a-charset")
        assert "revised quote" in parse_message(raw).body_text


class TestTheEventBody:
    def test_it_is_exactly_what_the_capture_api_takes(self) -> None:
        event = parse_message(a_message()).as_event()
        assert event["type"] == "EXTERNAL_MESSAGE"
        assert event["source_system"] == DEFAULT_SOURCE_SYSTEM
        assert event["source_ref"] == "abc123@example.test"
        assert event["title"] == "Revised quote"
        assert event["occurred_at"] == "2026-09-12T09:00:00+00:00"
        assert event["participants"][0] == {
            "role": "speaker",
            "external_handle": "mai@example.test",
        }

    def test_it_claims_nothing_about_origin_or_sensitivity(self) -> None:
        """A connector reports what it received. `origin` is the API's to decide, and a connector
        that could send `internal` could feed the system its own output (BR-E-11)."""
        event = parse_message(a_message()).as_event()
        assert "origin" not in event
        assert "sensitivity" not in event


class TestIdempotencyKey:
    def test_the_same_message_always_derives_the_same_key(self) -> None:
        assert parse_message(a_message()).idempotency_key == (
            parse_message(a_message()).idempotency_key
        )

    def test_an_edit_derives_a_different_key(self) -> None:
        """ADR-0058 §3. A correction carries the original's reference and is a revision, not a
        retry — so a key over the reference alone would refuse it as a changed body."""
        original = parse_message(a_message())
        edited = parse_message(a_message(body="Actually, Monday."))
        assert original.source_ref == edited.source_ref
        assert original.idempotency_key != edited.idempotency_key

    def test_the_same_bytes_from_two_channels_are_two_actions(self) -> None:
        assert idempotency_key("email.imap", "m1", "hello") != idempotency_key(
            "email.support", "m1", "hello"
        )


class TestAttachments:
    """CP23 carries the files; CP21 only named them."""

    def an_attached_message(self, size: int = 19) -> bytes:
        import base64

        blob = base64.b64encode(b"%PDF-1.4" + b"x" * (size - 8)).decode()
        return (
            "From: mai@example.test\r\nTo: khoa@example.test\r\n"
            "Message-ID: <att@example.test>\r\n"
            "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
            "--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            "The quote is attached.\r\n"
            "--b\r\nContent-Type: application/pdf\r\n"
            'Content-Disposition: attachment; filename="quote.pdf"\r\n'
            "Content-Transfer-Encoding: base64\r\n\r\n"
            f"{blob}\r\n"
            "--b--\r\n"
        ).encode()

    def test_the_file_arrives_with_its_bytes(self) -> None:
        attachments = parse_message(self.an_attached_message()).attachments
        assert len(attachments) == 1
        assert attachments[0].filename == "quote.pdf"
        assert attachments[0].media_type == "application/pdf"
        assert attachments[0].content.startswith(b"%PDF-1.4")
        assert attachments[0].size_bytes == 19

    def test_the_body_is_the_message_and_not_the_attachment(self) -> None:
        message = parse_message(self.an_attached_message())
        assert message.body_text.strip() == "The quote is attached."
        assert "PDF" not in message.body_text

    def test_the_event_body_says_nothing_about_them(self) -> None:
        """An attachment is attached *to* an Event, not described inside one (ADR-0039).

        Naming files in `body_text` would put words into a record nobody wrote, and Evidence is
        quoted from it (BR-E-05).
        """
        event = parse_message(self.an_attached_message()).as_event()
        assert "quote.pdf" not in str(event)
        assert "attachments" not in event

    def test_a_message_with_no_attachment_carries_none(self) -> None:
        assert parse_message(a_message()).attachments == ()

    def test_an_oversized_file_is_skipped_rather_than_truncated(self) -> None:
        """Half a PDF is not a smaller PDF. An Event with a corrupt attachment is worse than one
        that says a file was too large."""
        raw = self.an_attached_message(size=4096)
        assert parse_message(raw, max_attachment_bytes=100).attachments == ()
        assert len(parse_message(raw, max_attachment_bytes=1_000_000).attachments) == 1

    def test_a_filename_from_the_open_internet_is_made_safe(self) -> None:
        """It goes into a record and into object metadata; path separators are not names."""
        raw = (
            b"From: a@x.test\r\nTo: b@x.test\r\n"
            b"Message-ID: <evil@x.test>\r\n"
            b"Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
            b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
            b"--b\r\nContent-Type: text/plain\r\n\r\nhello\r\n"
            b"--b\r\nContent-Type: application/pdf\r\n"
            b'Content-Disposition: attachment; filename="../../etc/passwd"\r\n\r\n'
            b"not a pdf\r\n"
            b"--b--\r\n"
        )
        assert parse_message(raw).attachments[0].filename == "passwd"

    def test_an_inline_part_with_no_filename_is_body_not_attachment(self) -> None:
        """The same test decides both, so the two cannot disagree about which parts are prose."""
        message = parse_message(a_message())
        assert message.attachments == ()
        assert "revised quote" in message.body_text


class TestTheConversation:
    """ADR-0072, BR-E-19.

    `thread_ref` is what lets WorkOS say "this message and that work item came from the same
    conversation" without comparing any text. So the only acceptable source for it is what the
    headers state — everything here is about refusing to be clever.
    """

    def test_a_message_that_replies_to_nothing_is_a_thread_of_one(self) -> None:
        """Its own identifier. Not `None`: a first message *is* a conversation, and the reply that
        arrives tomorrow has to land in the same one."""
        message = parse_message(a_message())
        assert message.thread_ref == "abc123@example.test"
        assert message.thread_ref == message.source_ref

    def test_the_root_of_references_wins(self) -> None:
        """RFC 5322 §3.6.4 orders `References` oldest first, so the first entry is the message that
        started the thread — stable for every message in it, however deep the reply chain goes."""
        message = parse_message(
            a_message(
                headers=(
                    "From: Mai Tran <Mai@Example.test>\r\n"
                    "Subject: Re: Revised quote\r\n"
                    "Message-ID: <reply9@example.test>\r\n"
                    "References: <root1@example.test> <middle5@example.test>\r\n"
                    "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
                )
            )
        )
        assert message.thread_ref == "root1@example.test"

    def test_in_reply_to_is_the_fallback(self) -> None:
        """A client that sends only `In-Reply-To` names the parent rather than the root. That makes
        a shorter thread, not a wrong one, which is the right way to be incomplete here."""
        message = parse_message(
            a_message(
                headers=(
                    "From: Mai Tran <Mai@Example.test>\r\n"
                    "Subject: Re: Revised quote\r\n"
                    "Message-ID: <reply9@example.test>\r\n"
                    "In-Reply-To: <root1@example.test>\r\n"
                    "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
                )
            )
        )
        assert message.thread_ref == "root1@example.test"

    def test_the_subject_is_never_the_thread(self) -> None:
        """Two unrelated messages with the same subject must not become one conversation. This is
        the failure BR-E-19 names, and it is the one that would produce wrong *links* rather than
        merely wrong grouping."""
        first = parse_message(
            a_message(
                headers=(
                    "From: a@example.test\r\nSubject: Re: update\r\n"
                    "Message-ID: <one@example.test>\r\n"
                    "Date: Sat, 12 Sep 2026 09:00:00 +0000\r\n"
                )
            )
        )
        second = parse_message(
            a_message(
                headers=(
                    "From: b@example.test\r\nSubject: Re: update\r\n"
                    "Message-ID: <two@example.test>\r\n"
                    "Date: Sat, 12 Sep 2026 10:00:00 +0000\r\n"
                )
            )
        )
        assert first.thread_ref != second.thread_ref

    def test_it_travels_in_the_event_body(self) -> None:
        """The contract is seven fields and this is the eighth; a field the API never receives is a
        field that does not exist."""
        assert parse_message(a_message()).as_event()["thread_ref"] == "abc123@example.test"

    def test_a_root_and_its_reply_share_a_thread(self) -> None:
        """The property the whole feature rests on, asserted end to end rather than per-header.

        The root names its thread with its own `Message-ID`; the reply names it from `References`.
        Those are two different headers normalised by two different code paths, and the first
        version of this shipped with one stripping `<>` and the other not — so a message and its
        own replies landed in different conversations and nothing downstream could have noticed.
        """
        root = parse_message(a_message())
        reply = parse_message(
            a_message(
                headers=(
                    "From: khoa@example.test\r\nSubject: Re: Revised quote\r\n"
                    "Message-ID: <reply9@example.test>\r\n"
                    "References: <abc123@example.test>\r\n"
                    "Date: Sat, 12 Sep 2026 11:00:00 +0000\r\n"
                )
            )
        )
        assert root.thread_ref == reply.thread_ref
        assert root.source_ref != reply.source_ref, "two messages, one conversation"
