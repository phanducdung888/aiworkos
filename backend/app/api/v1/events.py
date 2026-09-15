"""The capture surface.

`POST /api/v1/events` is the path the product exists for: somebody pastes a conversation,
writes down what was said in a meeting, or forwards a message, and the system has a record of
it. Everything downstream — extraction, Evidence, Commitments — reads what lands here, so the
endpoint's job is to record faithfully and refuse anything it cannot record faithfully.

Three behaviours are worth knowing before reading the handlers.

*A repeat is not an error.* BR-E-02 makes ingestion idempotent on the external reference:
sending the same `source_ref` twice returns 200 and the Event that already exists. Sending
it with changed content returns 201 and a *new* Event marked as a revision of the original.
The original is never edited, because the record of what was first observed is the thing
being protected (ADR-0038).

*This is separate from HTTP idempotency.* `Idempotency-Key` replays an identical response for
a retried request; BR-E-02 deduplicates the same upstream message arriving twice through two
different requests. They answer different questions and both apply.

*Attachments do not pass through here.* ADR-0039: the API issues a presigned URL and the client
transfers directly to the store. Every attachment endpoint loads the Event first and authorizes
against it, so attachment access is Event access with no second permission to get out of step.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    AttachmentContentResource,
    AttachmentStart,
    AttachmentTicketResource,
    EventAttachmentResource,
    EventCapture,
    EventDetail,
    EventList,
    EventParticipantResource,
    EventResource,
)
from app.contexts.signal.public import (
    AttachmentService,
    CaptureEvent,
    CaptureService,
    CompleteAttachment,
    EventFilter,
    EventType,
    ParticipantInput,
    ProcessingStatus,
    RequestAttachmentContent,
    ServiceContext,
    StartAttachment,
    attachments_for,
    list_events,
    participants_for,
)
from app.contexts.signal.public import read_event as read_readable_event
from app.platform.authz import Action, ResourceType
from app.platform.errors import EntityNotFound
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    ObjectStoreDep,
    PrincipalDep,
    SessionDep,
    may_reach,
)
from app.platform.http.etag import etag_for
from app.platform.http.idempotency import Idempotency, replay_response
from app.platform.http.pagination import clamp_limit, decode_cursor
from app.platform.http.validation import CleanText

router = APIRouter(prefix="/api/v1/events", tags=["capture"], responses=PROBLEM_RESPONSES)


def _context(
    session: SessionDep, principal: PrincipalDep, actor: ActorDep, store: ObjectStoreDep
) -> ServiceContext:
    return ServiceContext(
        session=session, principal=principal, actor=actor, object_store=store
    )


def _detail(session: SessionDep, principal: PrincipalDep, event: Any) -> EventDetail:
    """One Event with the two collections that are part of reading it, not separate resources.

    A participant list is meaningless without its Event and is never paged, so a second round trip
    would buy nothing. Attachments are listed as metadata only; fetching one is its own request
    because that is where a URL gets issued and therefore where authorization must run again.
    """
    detail = EventDetail.model_validate(event)
    detail.participants = [
        EventParticipantResource.model_validate(row)
        for row in participants_for(session, org_id=principal.org_id, event_id=event.id)
    ]
    detail.attachments = [
        EventAttachmentResource.model_validate(row)
        for row in attachments_for(session, org_id=principal.org_id, event_id=event.id)
    ]
    return detail


def _load_readable(session: SessionDep, principal: PrincipalDep, event_id: uuid.UUID) -> Any:
    """Read-filtered, so an Event that exists but may not be read is indistinguishable from one
    that does not (BR-E-08)."""
    event = read_readable_event(session, principal, event_id)
    if event is None:
        raise EntityNotFound("event", event_id)
    return event


@router.post("", response_model=EventDetail)
def capture_event(
    body: EventCapture,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    store: ObjectStoreDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """Capture an Event. 201 for a new one, 200 for a reference already seen (BR-E-02)."""
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint="POST /api/v1/events",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    result = CaptureService(_context(session, principal, actor, store)).capture(
        CaptureEvent(
            source_system=body.source_system,
            event_type=body.type,
            occurred_at=body.occurred_at,
            title=body.title,
            body_text=body.body_text,
            source_ref=body.source_ref,
            thread_ref=body.thread_ref,
            channel=body.channel,
            sender_external_id=body.sender_external_id,
            sensitivity=body.sensitivity,
            participants=tuple(
                ParticipantInput(
                    role=participant.role,
                    person_id=participant.person_id,
                    external_handle=participant.external_handle,
                    match_confidence=participant.confidence,
                )
                for participant in body.participants
            ),
        )
    )
    detail = _detail(session, principal, result.event)
    code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    response.status_code = code
    guard.record(code, detail.model_dump(mode="json"))
    if result.created:
        response.headers["Location"] = f"/api/v1/events/{result.event.id}"
    response.headers["ETag"] = etag_for(result.event.version)
    return detail


@router.get("", response_model=EventList)
def list_captured_events(
    session: SessionDep,
    principal: PrincipalDep,
    type: EventType | None = None,
    source_system: Annotated[CleanText | None, Query()] = None,
    occurred_after: dt.datetime | None = None,
    occurred_before: dt.datetime | None = None,
    participant_person_id: uuid.UUID | None = None,
    processing_status: ProcessingStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> EventList:
    """Newest first. Restricted Events the caller is not part of are absent, not forbidden."""
    # `EVENT.LIST` is a cell of its own and the matrix denies it to `ingestion`, which reads what
    # it captured one Event at a time. The row narrowing below would have produced the right rows
    # anyway — it is derived from `EVENT.READ` — but answering a question the matrix refuses is
    # not something to leave resting on a predicate that happens to agree.
    may_reach(principal, Action.LIST, ResourceType.EVENT)
    page = list_events(
        session,
        principal,
        filters=EventFilter(
            event_type=type,
            source_system=source_system,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            participant_person_id=participant_person_id,
            processing_status=processing_status,
        ),
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return EventList(
        items=[EventResource.model_validate(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{event_id}", response_model=EventDetail)
def read_event(
    event_id: uuid.UUID, response: Response, session: SessionDep, principal: PrincipalDep
) -> Any:
    event = _load_readable(session, principal, event_id)
    response.headers["ETag"] = etag_for(event.version)
    return _detail(session, principal, event)


# --------------------------------------------------------------------------- attachments


@router.post(
    "/{event_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    response_model=AttachmentTicketResource,
)
def start_attachment(
    event_id: uuid.UUID,
    body: AttachmentStart,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    store: ObjectStoreDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """Reserve an attachment and get a URL to upload it to (ADR-0039).

    The row is created `pending`. It becomes readable only after the complete call has asked the
    store how big the object actually is, so a client that never finishes leaves a row that is
    visible as unfinished rather than one that claims a file nobody can fetch.
    """
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint=f"POST /api/v1/events/{event_id}/attachments",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    ticket = AttachmentService(_context(session, principal, actor, store)).start(
        StartAttachment(
            event_id=event_id, filename=body.filename, media_type=body.media_type
        )
    )
    resource = AttachmentTicketResource(
        attachment=EventAttachmentResource.model_validate(ticket.attachment),
        upload_url=ticket.upload_url,
        expires_at=ticket.expires_at,
    )
    guard.record(201, resource.model_dump(mode="json"))
    return resource


@router.post(
    "/{event_id}/attachments/{attachment_id}/complete",
    response_model=EventAttachmentResource,
)
def complete_attachment(
    event_id: uuid.UUID,
    attachment_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    store: ObjectStoreDep,
) -> Any:
    """Record what the store actually holds.

    Size and checksum are read from the object, never taken from the request body (ADR-0039).
    """
    return AttachmentService(_context(session, principal, actor, store)).complete(
        CompleteAttachment(event_id=event_id, attachment_id=attachment_id)
    )


@router.get(
    "/{event_id}/attachments/{attachment_id}/content",
    response_model=AttachmentContentResource,
)
def attachment_content(
    event_id: uuid.UUID,
    attachment_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    store: ObjectStoreDep,
) -> Any:
    """A short-lived download URL, issued only to a caller who may read the Event.

    Returns the URL rather than redirecting to it: a 302 to a presigned link puts a credential in
    the browser's history and in every intermediary's access log, and the caller here is an
    application that is about to fetch it anyway.
    """
    content = AttachmentService(_context(session, principal, actor, store)).content_url(
        RequestAttachmentContent(event_id=event_id, attachment_id=attachment_id)
    )
    return AttachmentContentResource(
        attachment=EventAttachmentResource.model_validate(content.attachment),
        download_url=content.download_url,
        expires_at=content.expires_at,
    )
