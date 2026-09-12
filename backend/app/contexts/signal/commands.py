"""Command objects for Signal/Capture.

Frozen dataclasses rather than Pydantic models: these are the boundary between the API layer and the
services, and keeping them plain means the domain never depends on how a request happened to arrive.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

from app.contexts.signal.domain import (
    EventOrigin,
    EventType,
    ParticipantRole,
    Sensitivity,
)


@dataclasses.dataclass(frozen=True, slots=True)
class ParticipantInput:
    """Somebody named on an Event.

    Both halves are optional individually and at least one is required (BR-E-12): a meeting has
    attendees the system can name, and a WhatsApp thread has numbers it cannot yet.
    """

    role: ParticipantRole
    person_id: uuid.UUID | None = None
    external_handle: str | None = None
    match_confidence: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class CaptureEvent:
    source_system: str
    event_type: EventType
    occurred_at: dt.datetime
    title: str | None = None
    body_text: str | None = None
    source_ref: str | None = None
    channel: str | None = None
    sender_external_id: str | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL
    #: Always `external` from the capture API. The projector that writes internal Events is a later
    #: checkpoint and does not come through here (BR-E-10).
    origin: EventOrigin = EventOrigin.EXTERNAL
    origin_domain_event_id: uuid.UUID | None = None
    participants: tuple[ParticipantInput, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class StartAttachment:
    event_id: uuid.UUID
    filename: str
    media_type: str


@dataclasses.dataclass(frozen=True, slots=True)
class CompleteAttachment:
    event_id: uuid.UUID
    attachment_id: uuid.UUID


@dataclasses.dataclass(frozen=True, slots=True)
class RequestAttachmentContent:
    event_id: uuid.UUID
    attachment_id: uuid.UUID
