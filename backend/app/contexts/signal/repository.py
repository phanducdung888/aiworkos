"""Data access for Signal/Capture. SQL lives here and nowhere above it.

Every function takes `org_id` explicitly and filters on it, even though RLS would refuse a
cross-tenant row anyway. Two mechanisms, and neither is the excuse for dropping the other
(BR-G-01a): RLS is what holds when a query is written carelessly, the predicate is what makes the
query say what it means.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.contexts.signal.models import Event, EventAttachment, EventParticipant, Evidence
from app.platform.ids import uuid7


def get_event(session: Session, *, org_id: uuid.UUID, event_id: uuid.UUID) -> Event | None:
    """A soft-deleted Event reads as absent (BR-E-07).

    The row survives for audit; it stops being readable as an Event.
    """
    return session.scalars(
        select(Event).where(
            Event.org_id == org_id, Event.id == event_id, Event.deleted_at.is_(None)
        )
    ).one_or_none()


def find_by_source_ref(
    session: Session, *, org_id: uuid.UUID, source_system: str, source_ref: str
) -> Event | None:
    """The original under a given external reference, for BR-E-02.

    Revisions are excluded: a chain of corrections still has exactly one original, and that is the
    row a repeat is compared against.
    """
    return session.scalars(
        select(Event).where(
            Event.org_id == org_id,
            Event.source_system == source_system,
            Event.source_ref == source_ref,
            Event.revision_of_event_id.is_(None),
            Event.deleted_at.is_(None),
        )
    ).one_or_none()


def insert_event(
    session: Session,
    *,
    org_id: uuid.UUID,
    source_system: str,
    source_ref: str | None,
    thread_ref: str | None,
    content_hash: str,
    origin: str,
    origin_domain_event_id: uuid.UUID | None,
    revision_of_event_id: uuid.UUID | None,
    event_type: str,
    channel: str | None,
    sender_external_id: str | None,
    occurred_at: dt.datetime,
    title: str | None,
    body_text: str | None,
    raw_payload_uri: str | None,
    sensitivity: str,
    captured_by_person_id: uuid.UUID | None,
) -> Event:
    event = Event(
        id=uuid7(),
        org_id=org_id,
        source_system=source_system,
        source_ref=source_ref,
        thread_ref=thread_ref,
        content_hash=content_hash,
        origin=origin,
        origin_domain_event_id=origin_domain_event_id,
        revision_of_event_id=revision_of_event_id,
        type=event_type,
        channel=channel,
        sender_external_id=sender_external_id,
        occurred_at=occurred_at,
        title=title,
        body_text=body_text,
        raw_payload_uri=raw_payload_uri,
        sensitivity=sensitivity,
        captured_by_person_id=captured_by_person_id,
    )
    session.add(event)
    session.flush()
    return event


def insert_participant(
    session: Session,
    *,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    person_id: uuid.UUID | None,
    external_handle: str | None,
    role: str,
    match_confidence: int,
    resolved_at: dt.datetime | None,
) -> EventParticipant:
    participant = EventParticipant(
        id=uuid7(),
        org_id=org_id,
        event_id=event_id,
        person_id=person_id,
        external_handle=external_handle,
        role=role,
        match_confidence=match_confidence,
        resolved_at=resolved_at,
    )
    session.add(participant)
    session.flush()
    return participant


def participants_for(
    session: Session, *, org_id: uuid.UUID, event_id: uuid.UUID
) -> Sequence[EventParticipant]:
    return session.scalars(
        select(EventParticipant)
        .where(EventParticipant.org_id == org_id, EventParticipant.event_id == event_id)
        .order_by(EventParticipant.created_at)
    ).all()


def set_participant_count(
    session: Session, *, org_id: uuid.UUID, event_id: uuid.UUID, count: int
) -> None:
    """One of the few columns ADR-0038 leaves writable, and it is written only from here."""
    session.execute(
        update(Event)
        .where(Event.org_id == org_id, Event.id == event_id)
        .values(participant_count=count)
    )


def is_participant(
    session: Session, *, org_id: uuid.UUID, event_id: uuid.UUID, person_id: uuid.UUID
) -> bool:
    """BR-E-08. Whether this person is named on this Event, at all, in any role."""
    return (
        session.scalars(
            select(EventParticipant.id).where(
                EventParticipant.org_id == org_id,
                EventParticipant.event_id == event_id,
                EventParticipant.person_id == person_id,
            )
        ).first()
        is not None
    )


# --------------------------------------------------------------------------- attachments


def insert_attachment(
    session: Session,
    *,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    attachment_id: uuid.UUID,
    object_key: str,
    filename: str,
    media_type: str,
    uploaded_by_person_id: uuid.UUID | None,
) -> EventAttachment:
    attachment = EventAttachment(
        id=attachment_id,
        org_id=org_id,
        event_id=event_id,
        object_key=object_key,
        filename=filename,
        media_type=media_type,
        uploaded_by_person_id=uploaded_by_person_id,
    )
    session.add(attachment)
    session.flush()
    return attachment


def get_attachment(
    session: Session, *, org_id: uuid.UUID, attachment_id: uuid.UUID
) -> EventAttachment | None:
    return session.scalars(
        select(EventAttachment).where(
            EventAttachment.org_id == org_id, EventAttachment.id == attachment_id
        )
    ).one_or_none()


def attachments_for(
    session: Session, *, org_id: uuid.UUID, event_id: uuid.UUID
) -> Sequence[EventAttachment]:
    return session.scalars(
        select(EventAttachment)
        .where(EventAttachment.org_id == org_id, EventAttachment.event_id == event_id)
        .order_by(EventAttachment.created_at)
    ).all()


def mark_attachment_available(
    session: Session,
    *,
    attachment: EventAttachment,
    size_bytes: int,
    checksum: str,
    completed_at: dt.datetime,
) -> EventAttachment:
    attachment.size_bytes = size_bytes
    attachment.checksum = checksum
    attachment.status = "available"
    attachment.completed_at = completed_at
    attachment.version += 1
    session.flush()
    return attachment


# --------------------------------------------------------------------------- evidence


def insert_evidence(
    session: Session,
    *,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    locator: dict[str, object],
    excerpt: str | None,
    claim_summary: str | None,
    target_type: str,
    target_id: uuid.UUID,
    assertion: str,
    confidence: int,
    produced_by_type: str,
    produced_by_id: uuid.UUID | None,
) -> Evidence:
    evidence = Evidence(
        id=uuid7(),
        org_id=org_id,
        event_id=event_id,
        locator=locator,
        excerpt=excerpt,
        claim_summary=claim_summary,
        target_type=target_type,
        target_id=target_id,
        assertion=assertion,
        confidence=confidence,
        produced_by_type=produced_by_type,
        produced_by_id=produced_by_id,
    )
    session.add(evidence)
    session.flush()
    return evidence


def evidence_for_event(
    session: Session, *, org_id: uuid.UUID, event_id: uuid.UUID
) -> Sequence[Evidence]:
    return session.scalars(
        select(Evidence)
        .where(Evidence.org_id == org_id, Evidence.event_id == event_id)
        .order_by(Evidence.created_at)
    ).all()


def supersede_evidence(
    session: Session, *, org_id: uuid.UUID, evidence_id: uuid.UUID, superseded_by_id: uuid.UUID
) -> None:
    """BR-E-06. The only column the immutability trigger leaves writable on this table."""
    session.execute(
        update(Evidence)
        .where(Evidence.org_id == org_id, Evidence.id == evidence_id)
        .values(superseded_by_id=superseded_by_id)
    )


def get_evidence(
    session: Session, *, org_id: uuid.UUID, evidence_id: uuid.UUID
) -> Evidence | None:
    return session.scalars(
        select(Evidence).where(Evidence.org_id == org_id, Evidence.id == evidence_id)
    ).one_or_none()


def evidence_for_target(
    session: Session, *, org_id: uuid.UUID, target_type: str, target_id: uuid.UUID
) -> Sequence[Evidence]:
    """Every citation for one entity, superseded rows included.

    A superseded citation is not deleted and not hidden: BR-E-06 keeps it so that "we used to
    believe this because of that" stays answerable, which is the whole point of superseding rather
    than editing.
    """
    return session.scalars(
        select(Evidence)
        .where(
            Evidence.org_id == org_id,
            Evidence.target_type == target_type,
            Evidence.target_id == target_id,
        )
        .order_by(Evidence.created_at)
    ).all()
