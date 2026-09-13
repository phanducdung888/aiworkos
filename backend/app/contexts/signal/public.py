"""The published interface of Signal/Capture (ADR-0001).

Everything another context or the API layer is allowed to use. What is deliberately not here: the
models, the repository and the raw session work. A caller that needs an Event asks for one; a caller
that wants to write SQL against `event` is reaching into this context's internals and the import
contract will say so.

Evidence is absent too, and that absence is the current state of the design rather than an oversight
— the table and its invariants exist (migration 0009) so that Events captured now are citable later,
and nothing may create Evidence until extraction defines who produces it and under what authority.
"""

from app.contexts.signal.commands import (
    CaptureEvent,
    CompleteAttachment,
    CreateEvidence,
    ParticipantInput,
    RequestAttachmentContent,
    StartAttachment,
    SupersedeEvidence,
)
from app.contexts.signal.domain import (
    CAPTURABLE_TYPES,
    AttachmentStatus,
    EventOrigin,
    EventType,
    ParticipantRole,
    ProcessingStatus,
    Sensitivity,
    effective_attachment_status,
    may_extract,
    visible_to,
)
from app.contexts.signal.evidence import (
    Assertion,
    AttachmentLocator,
    EvidenceTarget,
    ProducedBy,
    TextLocator,
)
from app.contexts.signal.models import Event, EventAttachment, EventParticipant, Evidence
from app.contexts.signal.queries import EventFilter, EventPage, list_events
from app.contexts.signal.queries import get_event as read_event
from app.contexts.signal.references import assert_event_exists, assert_evidence_exists
from app.contexts.signal.repository import (
    attachments_for,
    evidence_for_target,
    get_evidence,
    participants_for,
)
from app.contexts.signal.services import (
    UPLOAD_WINDOW,
    AttachmentContent,
    AttachmentService,
    AttachmentTicket,
    CaptureResult,
    CaptureService,
    EvidenceService,
    ServiceContext,
)

__all__ = [
    "get_evidence",
    "evidence_for_target",
    "SupersedeEvidence",
    "EvidenceService",
    "Evidence",
    "CreateEvidence",
    "Assertion",
    "AttachmentLocator",
    "EvidenceTarget",
    "ProducedBy",
    "TextLocator",
    "assert_event_exists",
    "assert_evidence_exists",
    "CAPTURABLE_TYPES",
    "AttachmentContent",
    "AttachmentService",
    "AttachmentStatus",
    "AttachmentTicket",
    "CaptureEvent",
    "CaptureResult",
    "CaptureService",
    "CompleteAttachment",
    "Event",
    "EventAttachment",
    "EventFilter",
    "EventOrigin",
    "EventPage",
    "EventParticipant",
    "EventType",
    "ParticipantInput",
    "ParticipantRole",
    "ProcessingStatus",
    "RequestAttachmentContent",
    "Sensitivity",
    "ServiceContext",
    "StartAttachment",
    "attachments_for",
    "effective_attachment_status",
    "UPLOAD_WINDOW",
    "list_events",
    "may_extract",
    "participants_for",
    "read_event",
    "visible_to",
]
