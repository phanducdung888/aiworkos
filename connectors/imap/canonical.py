"""RFC 5322 message → the body of one `POST /api/v1/events` call.

Pure: bytes in, a dict out, no clock, no network, no configuration beyond what is passed. That is
what makes the interesting half of a connector testable without a mail server, and it is where
every decision about *what a message means structurally* is written down.

**Normalisation, not interpretation.** This module decides which header is the timestamp and which
addresses are participants. It does not decide who anybody is, what the message is about, whether it
contains a commitment, or which parts of it are worth keeping. Those are WorkOS's, and several of
them are the AI's — a connector that trimmed quoted replies or guessed a sender's identity would be
making judgements nobody could audit (ADR-0058).

**It refuses rather than invents.** A message with no usable timestamp or no readable body is not
delivered with a plausible substitute; `Unnormalisable` is raised and the caller reports it. A
`Date` this code made up would be indistinguishable from one somebody sent.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import email.message
import email.policy
import email.utils
import hashlib
import re
from email.parser import BytesParser
from typing import Any

#: The largest file this connector will carry, per attachment.
#:
#: A message is whole in memory by the time it is parsed, so this is what keeps that bounded. Ten
#: megabytes covers the quotes, decks and scanned invoices this exists for; anything larger is
#: skipped and reported rather than truncated.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

#: What this connector calls itself. Half of BR-E-02's key, so it is stable by contract: changing it
#: makes every message already delivered look like a message from somewhere else.
DEFAULT_SOURCE_SYSTEM = "email.imap"

#: Headers whose addresses become participants, and the role each implies.
#:
#: `Reply-To` is deliberately absent: it names where an answer should go, not who was present. `Bcc`
#: never survives delivery and would be a privacy leak if it did.
_PARTICIPANT_HEADERS: tuple[tuple[str, str], ...] = (
    ("From", "speaker"),
    ("To", "recipient"),
    ("Cc", "recipient"),
)

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t]*\n[ \t]*")


class Unnormalisable(Exception):
    """This message cannot be expressed as an Event without inventing something."""


@dataclasses.dataclass(frozen=True, slots=True)
class Attachment:
    """One file from a message, as the capture API will want it.

    The bytes are carried rather than streamed because a message is already whole in memory by the
    time it is parsed — IMAP hands over the entire RFC 5322 document — so pretending otherwise would
    be ceremony. `IMAP_MAX_ATTACHMENT_BYTES` is what stops that from being unbounded.
    """

    filename: str
    media_type: str
    content: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclasses.dataclass(frozen=True, slots=True)
class CanonicalMessage:
    """One message, in the shape the capture API takes.

    `idempotency_key` travels beside the body rather than inside it because it is a header, and it
    is derived here so that every caller — the delivery loop, a replay tool, a test — computes it
    the same way.
    """

    source_system: str
    source_ref: str
    occurred_at: dt.datetime
    title: str | None
    body_text: str
    participants: tuple[dict[str, str], ...]
    idempotency_key: str
    #: The files this message carried, with their bytes.
    #:
    #: Deliberately *not* in `as_event()`. Attachments are a separate flow — a presigned upload
    #: against the Event once it exists, not a field on it (ADR-0039) — and naming them in
    #: `body_text` would put words into the Event that nobody wrote, which Evidence is then quoted
    #: from (BR-E-05).
    attachments: tuple[Attachment, ...] = ()

    def as_event(self) -> dict[str, Any]:
        """The JSON body. `type` and `origin` are fixed: a connector reports what it received."""
        return {
            "type": "EXTERNAL_MESSAGE",
            "source_system": self.source_system,
            "source_ref": self.source_ref,
            "occurred_at": self.occurred_at.isoformat(),
            "title": self.title,
            "body_text": self.body_text,
            "participants": [dict(participant) for participant in self.participants],
        }


def parse_message(
    raw: bytes,
    *,
    source_system: str = DEFAULT_SOURCE_SYSTEM,
    received_at: dt.datetime | None = None,
    max_attachment_bytes: int = MAX_ATTACHMENT_BYTES,
) -> CanonicalMessage:
    """Normalise one message, or refuse it.

    `received_at` is the server's own INTERNALDATE, used only when the message carries no usable
    `Date`. It is a fact from the transport rather than a guess, which is the difference between a
    fallback and an invention.
    """
    parsed = BytesParser(policy=email.policy.default).parsebytes(raw)
    body = _newlines(_body_of(parsed))
    if not body.strip():
        raise Unnormalisable("the message has no readable text body")

    return CanonicalMessage(
        source_system=source_system,
        source_ref=_reference(parsed, raw),
        occurred_at=_occurred_at(parsed, received_at),
        title=_header(parsed, "Subject"),
        body_text=body,
        participants=_participants(parsed),
        idempotency_key=idempotency_key(source_system, _reference(parsed, raw), body),
        attachments=_attachments(parsed, limit=max_attachment_bytes),
    )


def idempotency_key(source_system: str, source_ref: str, body: str) -> str:
    """ADR-0058 §3: derived, and derived from the content as well as the reference.

    A random key per attempt would make every redelivery a new action, which is what the header
    exists to prevent. A key over the reference alone fails the other way: a corrected message
    carries the original's reference, and the guard would refuse it as the same key with a
    different body when it is a revision, not a retry.
    """
    material = f"{source_system}\x00{source_ref}\x00{body}".encode()
    return f"sha256:{hashlib.sha256(material).hexdigest()}"


def _reference(parsed: email.message.Message, raw: bytes) -> str:
    """`Message-ID`, which RFC 5322 defines to be globally unique — exactly `source_ref`.

    A message without one is non-conformant and does happen. The fallback is a digest of the bytes:
    still deterministic, so a redelivery of the same message produces the same reference and
    BR-E-02 still recognises it, and still unique enough not to collide with another message.
    """
    raw_id = _header(parsed, "Message-ID")
    if raw_id:
        return raw_id.strip().strip("<>")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _occurred_at(
    parsed: email.message.Message, received_at: dt.datetime | None
) -> dt.datetime:
    """When it was sent, in UTC. The `Date` header, or the server's own receipt time."""
    raw_date = _header(parsed, "Date")
    if raw_date:
        try:
            sent = email.utils.parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            sent = None
        if sent is not None:
            # A `Date` with no zone is a local time nobody can place. Treated as UTC rather than
            # as the connector's own timezone, which would make the same message land differently
            # depending on where it was ingested.
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=dt.UTC)
            return sent.astimezone(dt.UTC)
    if received_at is not None:
        return received_at.astimezone(dt.UTC)
    raise Unnormalisable("the message has no usable Date and no server receipt time")


def _participants(parsed: email.message.Message) -> tuple[dict[str, str], ...]:
    """Addresses as bare handles, never as people.

    A connector has no way to know who an address belongs to and no authority to assert it, so
    `person_id` never appears here. Resolution happens inside WorkOS, at capture, against confirmed
    `external_identity` rows (ADR-0054) — and an address nobody has vouched for resolves to nobody.

    Lowercased, because that is how addresses are compared everywhere in practice and because the
    same normalisation has to be used when the mapping row is created. The first role an address
    appears under wins: somebody in both `To` and `Cc` is one participant.
    """
    seen: dict[str, dict[str, str]] = {}
    for header, role in _PARTICIPANT_HEADERS:
        for _name, address in email.utils.getaddresses(parsed.get_all(header, [])):
            handle = address.strip().lower()
            if not handle or "@" not in handle or handle in seen:
                continue
            seen[handle] = {"role": role, "external_handle": handle}
    return tuple(seen.values())


def _body_of(parsed: email.message.Message) -> str:
    """The text of the message, preferring what was written as text.

    An HTML-only message is stripped to text rather than dropped: turning markup into the words it
    surrounds is a format change, which is normalisation. What is *not* done is any judgement about
    which words matter — quoted replies and signatures are delivered intact, because deciding a
    reply is not part of the message is exactly the interpretation a connector must not make.
    """
    if not parsed.is_multipart():
        text = _decode(parsed)
        return _strip_html(text) if parsed.get_content_type() == "text/html" else text

    html: str | None = None
    for part in parsed.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():  # an attachment, not the message
            continue
        if part.get_content_type() == "text/plain":
            return _decode(part)
        if part.get_content_type() == "text/html" and html is None:
            html = _decode(part)
    return _strip_html(html) if html else ""


def _attachments(
    parsed: email.message.Message, *, limit: int
) -> tuple[Attachment, ...]:
    """The files a message carried, with their bytes.

    A part is an attachment when it has a filename. That is the same test the body extraction uses
    to skip it, so the two cannot disagree about which parts are prose and which are files.

    Oversized parts are skipped rather than truncated. Half a PDF is not a smaller PDF, and an
    Event whose attachment is silently corrupt is worse than one that says a file was too large.
    """
    if not parsed.is_multipart():
        return ()
    found: list[Attachment] = []
    for part in parsed.walk():
        if part.get_content_maintype() == "multipart":
            continue
        name = part.get_filename()
        if not name:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload or len(payload) > limit:
            continue
        found.append(
            Attachment(
                filename=_safe_filename(name),
                media_type=part.get_content_type() or "application/octet-stream",
                content=payload,
            )
        )
    return tuple(found)


def _safe_filename(name: str) -> str:
    """The name, with anything that is not a name taken out.

    A filename arrives from the open internet and goes into a record and into an object key's
    metadata. Path separators and NUL bytes are removed here rather than trusted to be harmless
    downstream, and an empty result is given a name rather than passed on as one.
    """
    cleaned = name.replace("\\", "/").split("/")[-1].replace("\x00", "").strip()
    return cleaned[:255] or "attachment"


def _decode(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        content = part.get_payload()
        return content if isinstance(content, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        # A charset this build does not know. Replacing is honest; guessing another would corrupt
        # the text silently, and the excerpt has to be quotable (BR-E-05).
        return payload.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    """Minimal and deliberately unclever. Tags out, entities left to the parser above."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = _TAG.sub("", text)
    return _WHITESPACE.sub("\n", text).strip()


def _newlines(text: str) -> str:
    """CRLF to LF.

    A format change, and one worth making: the stored body is what Evidence is quoted from and what
    span offsets index into (BR-E-05), so leaving wire line endings in would put a stray character
    inside every citation that crosses a line.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _header(parsed: email.message.Message, name: str) -> str | None:
    value = parsed.get(name)
    if value is None:
        return None
    # Header values arrive as policy objects that stringify to the decoded text.
    text = str(value).strip()
    return text or None
