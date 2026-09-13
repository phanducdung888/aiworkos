"""Event rules (BR-E-01 to BR-E-16). No I/O, no ORM, no session.

The Event is the system's record of something that happened, and almost every rule here exists to
keep that record honest rather than to keep it tidy. Two are worth reading before the rest:

BR-E-02 makes ingestion idempotent on the external reference, and makes a *differing* payload under
the same reference a revision rather than a conflict. An upstream system that edits a message in
place is not lying to us; it is telling us the message changed, and both versions are true of their
own moment.

BR-E-13 separates the capturer from the authors. Pasting a colleague's message records that you
captured it, never that you wrote it. Everything downstream — attribution, commitments, who gets
asked about a promise — depends on that distinction surviving capture.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import uuid

from app.platform.errors import DomainRuleViolation

#: BR-E-09. Clocks disagree and time zones get applied twice; a day is generous room for both. Much
#: beyond it and the "occurrence" is a scheduling intention, which is a different entity.
MAX_FUTURE_SKEW = dt.timedelta(hours=24)

#: The six binding types (Resolution Pack v1.1). The enum may grow; these do not shrink.
class EventType(enum.StrEnum):
    EXTERNAL_MESSAGE = "EXTERNAL_MESSAGE"
    MANUAL_CAPTURE = "MANUAL_CAPTURE"
    MEETING_NOTE = "MEETING_NOTE"
    COMMENT = "COMMENT"
    ATTACHMENT = "ATTACHMENT"
    SYSTEM_ACTIVITY = "SYSTEM_ACTIVITY"


class EventOrigin(enum.StrEnum):
    EXTERNAL = "external"
    INTERNAL = "internal"


class Sensitivity(enum.StrEnum):
    NORMAL = "normal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ProcessingStatus(enum.StrEnum):
    RECEIVED = "received"
    NORMALISED = "normalised"
    EXTRACTED = "extracted"
    FAILED = "failed"
    SKIPPED = "skipped"


class ParticipantRole(enum.StrEnum):
    ORGANISER = "organiser"
    SPEAKER = "speaker"
    MENTIONED = "mentioned"
    RECIPIENT = "recipient"


class AttachmentStatus(enum.StrEnum):
    PENDING = "pending"
    AVAILABLE = "available"
    PURGED = "purged"
    #: Reserved, never uploaded, and now past the window in which it could be. **Reported, never
    #: stored** — the column's CHECK constraint knows the three above and nothing writes this one.
    #:
    #: A reservation is a row plus a presigned URL with an expiry. When that expiry passes with no
    #: object behind it, the row can never become `available` by any action the holder can still
    #: take, and calling it `pending` for ever says the opposite. CP24 left four of these in the
    #: pilot database and CP25 had to answer what they meant.
    #:
    #: Derived rather than swept, for the reason every other derived fact in this system is
    #: (CLAUDE.md rule 5): it is a deterministic function of two columns and the clock, it needs no
    #: scheduler to be true, and it cannot drift from the thing it describes.
    EXPIRED = "expired"


def effective_attachment_status(
    status: str, created_at: dt.datetime, *, window: dt.timedelta, now: dt.datetime
) -> str:
    """What a reservation's status actually is, as opposed to what is written down.

    Only ever widens `pending` to `expired`. `available` and `purged` are facts about what
    happened and no amount of elapsed time changes either.

    Being late here is generous on purpose: an upload that finished inside the window and is
    completed afterwards still succeeds, because `complete` asks the store rather than the clock.
    This decides what to *show* somebody, and "expired" is the honest answer to a file that never
    arrived.
    """
    if status != AttachmentStatus.PENDING.value:
        return status
    return (
        AttachmentStatus.EXPIRED.value
        if now - created_at > window
        else AttachmentStatus.PENDING.value
    )


#: Types a person may capture through the web surface.
#:
#: `SYSTEM_ACTIVITY` is absent because it describes something the system observed about itself, and
#: a human asserting one would be fabricating a machine's account of its own behaviour. The
#: projector that turns allow-listed DomainEvents into internal Events writes those (BR-E-10), and
#: it does not come through this API.
CAPTURABLE_TYPES = frozenset(
    {
        EventType.EXTERNAL_MESSAGE,
        EventType.MANUAL_CAPTURE,
        EventType.MEETING_NOTE,
        EventType.COMMENT,
        EventType.ATTACHMENT,
    }
)

#: BR-E-15. A comment is an Event about what somebody said inside the product, so it is internal by
#: definition and BR-E-11 keeps it away from extraction.
INTERNAL_ONLY_TYPES = frozenset({EventType.COMMENT, EventType.SYSTEM_ACTIVITY})


@dataclasses.dataclass(frozen=True, slots=True)
class ExistingEvent:
    """What the domain needs to know about an Event that is already stored."""

    id: uuid.UUID
    content_hash: str
    source_ref: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class CaptureDecision:
    """What BR-E-02 says to do with an incoming Event.

    Three outcomes, and the middle one is the one people forget: a repeat is not an error and not a
    new row. It is the same Event, and the caller is told which.
    """

    #: Insert a new Event with no `revision_of_event_id`.
    is_new: bool
    #: Return this existing Event unchanged — same reference, same content (BR-E-02).
    duplicate_of: uuid.UUID | None = None
    #: Insert a new Event recording that it revises this one — same reference, different content.
    revision_of: uuid.UUID | None = None


def content_hash_for(*, title: str | None, body_text: str | None) -> str:
    """A stable digest of what the Event says.

    Title and body are hashed with a separator that cannot occur in either half's encoding, so that
    moving text across the boundary changes the hash. Without it, `("ab", "c")` and `("a", "bc")`
    would be the same Event and BR-E-02 would call a genuine edit a duplicate.
    """
    digest = hashlib.sha256()
    digest.update((title or "").encode())
    digest.update(b"\x00")
    digest.update((body_text or "").encode())
    return digest.hexdigest()


def validate_capture(
    *,
    event_type: EventType,
    origin: EventOrigin,
    occurred_at: dt.datetime,
    now: dt.datetime,
    body_text: str | None,
    raw_payload_uri: str | None,
    source_ref: str | None,
    origin_domain_event_id: uuid.UUID | None,
) -> None:
    """Everything that must hold before an Event is written, in the order a reader would ask it."""
    # BR-E-03. An Event with neither text nor a payload records that something happened and nothing
    # about what — it cannot be read, quoted or extracted from.
    if not (body_text and body_text.strip()) and not raw_payload_uri:
        raise DomainRuleViolation(
            "BR-E-03", "an event must carry either body_text or a raw payload"
        )

    if occurred_at > now + MAX_FUTURE_SKEW:
        raise DomainRuleViolation(
            "BR-E-09", "occurred_at may not be more than 24 hours in the future"
        )

    # BR-E-10. `internal` means "projected from a DomainEvent", and the projection has to say which
    # one, or the loop-prevention rule in BR-E-11 has nothing to trace.
    if origin is EventOrigin.INTERNAL and origin_domain_event_id is None:
        raise DomainRuleViolation(
            "BR-E-10", "an internal event must name the domain event it was projected from"
        )
    if origin is EventOrigin.EXTERNAL and origin_domain_event_id is not None:
        raise DomainRuleViolation(
            "BR-E-10", "an external event is not projected from a domain event"
        )

    if event_type in INTERNAL_ONLY_TYPES and origin is not EventOrigin.INTERNAL:
        raise DomainRuleViolation(
            "BR-E-15", f"{event_type.value} events are internal by definition"
        )

    # A reference that is blank is not a reference, and would defeat the partial unique index that
    # makes BR-E-02 work by making every such row distinct.
    if source_ref is not None and not source_ref.strip():
        raise DomainRuleViolation("BR-E-02", "source_ref must be a non-empty reference or absent")


def assert_capturable(event_type: EventType) -> None:
    """BR-E-13. What a person is permitted to assert happened."""
    if event_type not in CAPTURABLE_TYPES:
        raise DomainRuleViolation(
            "BR-E-13",
            f"{event_type.value} is produced by the system and cannot be captured by a person",
        )


def decide_capture(
    *, source_ref: str | None, content_hash: str, existing: ExistingEvent | None
) -> CaptureDecision:
    """BR-E-02, the whole rule in one place.

    A manual capture has no reference and is therefore never a duplicate: two people writing down
    the same meeting wrote down two observations of it, and deciding they are one thing is a
    judgement the system is not entitled to make from a hash.
    """
    if source_ref is None or existing is None:
        return CaptureDecision(is_new=True)
    if existing.content_hash == content_hash:
        return CaptureDecision(is_new=False, duplicate_of=existing.id)
    return CaptureDecision(is_new=True, revision_of=existing.id)


def may_extract(origin: EventOrigin, sensitivity: Sensitivity) -> bool:
    """BR-E-11 loop prevention, and the BR-E-08 policy exclusion in one answer.

    Nothing calls this yet — extraction is a later checkpoint. It lives here because the rule is a
    property of the Event, and the place to state it is next to the Event, not inside whatever
    worker is eventually written. `tests/unit/test_signal_domain.py` holds it to BR-E-11.
    """
    if origin is not EventOrigin.EXTERNAL:
        return False
    return sensitivity is not Sensitivity.RESTRICTED


def validate_participant(
    *,
    person_id: uuid.UUID | None,
    external_handle: str | None,
    match_confidence: int,
) -> None:
    if person_id is None and not (external_handle and external_handle.strip()):
        raise DomainRuleViolation(
            "BR-E-12", "a participant must name a person or carry an external handle"
        )
    if not 0 <= match_confidence <= 100:
        raise DomainRuleViolation("BR-E-12", "match_confidence is a percentage")
    # Confidence is a statement about a resolution. Attaching one to a row that resolves to nobody
    # would make the number unreadable: confident about what?
    if person_id is None and match_confidence != 0:
        raise DomainRuleViolation(
            "BR-E-12", "match_confidence describes a resolution to a person"
        )


def visible_to(
    *,
    sensitivity: Sensitivity,
    is_participant: bool,
    is_auditor: bool,
) -> bool:
    """BR-E-08, stated once so the query predicate and any later check cannot disagree.

    `confidential` is deliberately not narrowed here. The rule names only `restricted`, and
    inventing a second tier of enforcement would mean the label means something nobody wrote down.
    """
    if sensitivity is not Sensitivity.RESTRICTED:
        return True
    return is_participant or is_auditor
