"""Application services for Signal/Capture.

Every mutation runs the same sequence and in this order: authorize, validate, persist, audit, emit.
Nothing publishes from inside the request — the outbox row and the state change commit together or
neither does (ADR-0004), which is what makes "the Event exists but nothing was told about it"
unrepresentable rather than merely unlikely.

Attachments never carry bytes through this process (ADR-0039). A service hands back a presigned URL
and the client talks to the store directly; what the service records afterwards is what the store
reports, not what the client claimed.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

import app.contexts.identity.public as identity
from app.contexts.signal import authorization, queries, repository
from app.contexts.signal.commands import (
    CaptureEvent,
    CompleteAttachment,
    ParticipantInput,
    RequestAttachmentContent,
    StartAttachment,
)
from app.contexts.signal.domain import (
    AttachmentStatus,
    EventOrigin,
    EventType,
    ExistingEvent,
    Sensitivity,
    assert_capturable,
    content_hash_for,
    decide_capture,
    validate_capture,
    validate_participant,
)
from app.contexts.signal.models import Event, EventAttachment, EventParticipant
from app.platform.actor import Actor
from app.platform.audit import record_audit
from app.platform.authz import Action, Decision, Principal, Relation, ResourceType, authorize
from app.platform.errors import DomainRuleViolation, EntityNotFound
from app.platform.ids import uuid7
from app.platform.outbox import append_domain_event
from app.platform.storage import ObjectStore, object_key_for

#: How long a presigned URL lives. Long enough for a large file on a poor connection, short enough
#: that a URL found in a log tomorrow is worthless (ADR-0039).
UPLOAD_WINDOW = dt.timedelta(minutes=15)
DOWNLOAD_WINDOW = dt.timedelta(minutes=5)


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceContext:
    session: Session
    principal: Principal
    actor: Actor
    object_store: ObjectStore


@dataclasses.dataclass(frozen=True, slots=True)
class CaptureResult:
    """What capture did, which the API needs in order to answer honestly.

    A repeat of an already-captured reference is a 200 pointing at the existing Event, not a 201
    claiming a new one was made (BR-E-02). The caller learns which happened from `created`.
    """

    event: Event
    created: bool
    revision_of: uuid.UUID | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class AttachmentTicket:
    attachment: EventAttachment
    upload_url: str
    expires_at: dt.datetime


@dataclasses.dataclass(frozen=True, slots=True)
class AttachmentContent:
    attachment: EventAttachment
    download_url: str
    expires_at: dt.datetime


class _SignalService:
    def __init__(self, context: ServiceContext) -> None:
        self._ctx = context

    @property
    def _session(self) -> Session:
        return self._ctx.session

    @property
    def _org_id(self) -> uuid.UUID:
        return self._ctx.principal.org_id

    @property
    def _actor_person_id(self) -> uuid.UUID | None:
        return self._ctx.actor.person_id

    def _authorize(
        self,
        action: Action,
        relations: frozenset[Relation],
        resource_id: uuid.UUID | None = None,
    ) -> Decision:
        return authorize(
            self._ctx.principal,
            action,
            authorization.ref(ResourceType.EVENT, self._org_id, relations, resource_id),
        )

    def _audit(
        self,
        *,
        action: Action,
        resource_type: ResourceType,
        resource_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        decision: Decision | None,
    ) -> None:
        record_audit(
            self._session,
            org_id=self._org_id,
            actor=self._ctx.actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
            decision=decision,
        )

    def _emit(
        self, event_type: str, aggregate_id: uuid.UUID, payload: dict[str, Any]
    ) -> None:
        append_domain_event(
            self._session,
            org_id=self._org_id,
            type=event_type,
            aggregate_type="event",
            aggregate_id=aggregate_id,
            payload=payload,
            actor=self._ctx.actor,
        )

    def _load_event(self, event_id: uuid.UUID) -> Event:
        """Through the read-filtered path, never the raw repository.

        This is the line that makes "attachment authorization is Event authorization" true rather
        than merely intended (ADR-0039). `repository.get_event` filters by organization and returns
        anything inside it; the sensitivity narrowing that BR-E-08 applies to reading lives in the
        query layer, so loading an Event any other way hands the attachment endpoints a `restricted`
        Event the caller may not read — and the `READ` authorization that follows would pass,
        because the matrix grant is organization-wide and the narrowing is not in the matrix.

        Caught by `test_an_attachment_on_a_restricted_event_follows_the_event`, which is the exact
        shape of the bug this design claims to make unrepresentable.
        """
        event = queries.get_event(self._session, self._ctx.principal, event_id)
        if event is None:
            raise EntityNotFound("event", event_id)
        return event


def _snapshot(event: Event) -> dict[str, Any]:
    """What audit records about an Event.

    `body_text` is deliberately absent. The audit trail says an Event was captured, by whom, of what
    type and from where; copying the content into `audit_entry` would put a second, permanent copy
    of every captured message in a table that retention cannot purge (BR-E-07) and that RLS protects
    but nobody can edit. The Event itself is the record of the content.
    """
    return {
        "id": str(event.id),
        "type": event.type,
        "origin": event.origin,
        "source_system": event.source_system,
        "source_ref": event.source_ref,
        "content_hash": event.content_hash,
        "sensitivity": event.sensitivity,
        "occurred_at": event.occurred_at.isoformat(),
        "revision_of_event_id": (
            str(event.revision_of_event_id) if event.revision_of_event_id else None
        ),
        "captured_by_person_id": (
            str(event.captured_by_person_id) if event.captured_by_person_id else None
        ),
        "participant_count": event.participant_count,
    }


def _attachment_snapshot(attachment: EventAttachment) -> dict[str, Any]:
    return {
        "id": str(attachment.id),
        "event_id": str(attachment.event_id),
        "filename": attachment.filename,
        "media_type": attachment.media_type,
        "status": attachment.status,
        "size_bytes": attachment.size_bytes,
        "checksum": attachment.checksum,
    }


class CaptureService(_SignalService):
    def capture(self, command: CaptureEvent) -> CaptureResult:
        decision = self._authorize(Action.CREATE, authorization.capture_relations())

        # BR-E-13: a person may assert that a message, a note or a meeting happened. They may not
        # assert what the system observed about itself.
        assert_capturable(command.event_type)

        now = dt.datetime.now(dt.UTC)
        validate_capture(
            event_type=command.event_type,
            origin=command.origin,
            occurred_at=command.occurred_at,
            now=now,
            body_text=command.body_text,
            raw_payload_uri=None,
            source_ref=command.source_ref,
            origin_domain_event_id=command.origin_domain_event_id,
        )
        for participant in command.participants:
            validate_participant(
                person_id=participant.person_id,
                external_handle=participant.external_handle,
                match_confidence=participant.match_confidence,
            )
        # Through the published interface, never by reading Identity's tables (ADR-0035). A
        # participant naming somebody who is not in this organization is a bad reference, and the
        # caller is told which field rather than being handed a foreign key violation.
        for index, participant in enumerate(command.participants):
            if participant.person_id is not None:
                identity.assert_person_exists(
                    self._session,
                    org_id=self._org_id,
                    person_id=participant.person_id,
                    field=f"participants[{index}].person_id",
                )

        content_hash = content_hash_for(title=command.title, body_text=command.body_text)
        existing = None
        if command.source_ref is not None:
            found = repository.find_by_source_ref(
                self._session,
                org_id=self._org_id,
                source_system=command.source_system,
                source_ref=command.source_ref,
            )
            if found is not None:
                existing = ExistingEvent(
                    id=found.id, content_hash=found.content_hash, source_ref=found.source_ref
                )

        outcome = decide_capture(
            source_ref=command.source_ref, content_hash=content_hash, existing=existing
        )
        if outcome.duplicate_of is not None:
            # BR-E-02. Nothing is written, nothing is audited and nothing is emitted: the caller is
            # being told about an Event that already exists, which is not an occurrence of anything.
            return CaptureResult(
                event=self._load_event(outcome.duplicate_of), created=False
            )

        event = repository.insert_event(
            self._session,
            org_id=self._org_id,
            source_system=command.source_system,
            source_ref=command.source_ref,
            content_hash=content_hash,
            origin=command.origin.value,
            origin_domain_event_id=command.origin_domain_event_id,
            revision_of_event_id=outcome.revision_of,
            event_type=command.event_type.value,
            channel=command.channel,
            sender_external_id=command.sender_external_id,
            occurred_at=command.occurred_at,
            title=command.title,
            body_text=command.body_text,
            raw_payload_uri=None,
            sensitivity=command.sensitivity.value,
            captured_by_person_id=self._actor_person_id,
        )
        self._add_participants(event, command.participants)

        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.EVENT,
            resource_id=event.id,
            before=None,
            after=_snapshot(event),
            decision=decision,
        )
        self._emit(
            "EventRevised" if outcome.revision_of else "EventCaptured",
            event.id,
            {
                "event_id": str(event.id),
                "type": event.type,
                "origin": event.origin,
                "source_system": event.source_system,
                "occurred_at": event.occurred_at.isoformat(),
                "revision_of_event_id": (
                    str(outcome.revision_of) if outcome.revision_of else None
                ),
                # BR-E-11. Stated on the message so a consumer never has to re-derive it and
                # cannot get it wrong: an internal Event must not reach extraction.
                "extractable": event.origin == EventOrigin.EXTERNAL.value
                and event.sensitivity != Sensitivity.RESTRICTED.value,
            },
        )
        return CaptureResult(event=event, created=True, revision_of=outcome.revision_of)

    def _add_participants(
        self, event: Event, participants: tuple[ParticipantInput, ...]
    ) -> list[EventParticipant]:
        rows = [
            repository.insert_participant(
                self._session,
                org_id=self._org_id,
                event_id=event.id,
                person_id=participant.person_id,
                external_handle=participant.external_handle,
                role=participant.role.value,
                match_confidence=participant.match_confidence,
                # Resolution is recorded only when the caller actually resolved somebody. A
                # participant supplied as a bare handle is unresolved and must look unresolved.
                resolved_at=dt.datetime.now(dt.UTC) if participant.person_id else None,
            )
            for participant in participants
        ]
        if rows:
            repository.set_participant_count(
                self._session, org_id=self._org_id, event_id=event.id, count=len(rows)
            )
            self._session.refresh(event)
        return rows


class AttachmentService(_SignalService):
    """ADR-0039. Every method here loads the Event first and authorizes against it.

    That is the whole security design: there is no attachment permission to get wrong, because an
    attachment has no permissions of its own.
    """

    def start(self, command: StartAttachment) -> AttachmentTicket:
        event = self._load_event(command.event_id)
        decision = self._authorize(
            Action.ATTACH,
            authorization.event_relations(event, actor_person_id=self._actor_person_id),
            event.id,
        )
        if not command.filename.strip():
            raise DomainRuleViolation("BR-E-14", "an attachment must have a filename")

        attachment_id = uuid7()
        # Derived, never accepted. A client that could name the key could ask for a URL to somebody
        # else's object and the Event authorization above would be beside the point.
        key = object_key_for(
            org_id=self._org_id, event_id=event.id, attachment_id=attachment_id
        )
        attachment = repository.insert_attachment(
            self._session,
            org_id=self._org_id,
            event_id=event.id,
            attachment_id=attachment_id,
            object_key=key,
            filename=command.filename.strip(),
            media_type=command.media_type,
            uploaded_by_person_id=self._actor_person_id,
        )
        url = self._ctx.object_store.presigned_put(
            key, media_type=command.media_type, expires_in=UPLOAD_WINDOW
        )
        self._audit(
            action=Action.ATTACH,
            resource_type=ResourceType.EVENT,
            resource_id=event.id,
            before=None,
            after=_attachment_snapshot(attachment),
            decision=decision,
        )
        self._emit(
            "EventAttachmentStarted",
            event.id,
            {
                "event_id": str(event.id),
                "attachment_id": str(attachment.id),
                "filename": attachment.filename,
                "media_type": attachment.media_type,
            },
        )
        return AttachmentTicket(
            attachment=attachment,
            upload_url=url,
            expires_at=dt.datetime.now(dt.UTC) + UPLOAD_WINDOW,
        )

    def complete(self, command: CompleteAttachment) -> EventAttachment:
        event = self._load_event(command.event_id)
        decision = self._authorize(
            Action.ATTACH,
            authorization.event_relations(event, actor_person_id=self._actor_person_id),
            event.id,
        )
        attachment = self._load_attachment(event, command.attachment_id)

        if attachment.status == AttachmentStatus.AVAILABLE.value:
            # Completing twice is the client retrying, not an error. The recorded numbers are
            # already the store's, so there is nothing to redo and nothing to change.
            return attachment

        stored = self._ctx.object_store.stat(attachment.object_key)
        if stored is None:
            raise DomainRuleViolation(
                "BR-E-14", "no object was uploaded for this attachment"
            )

        before = _attachment_snapshot(attachment)
        updated = repository.mark_attachment_available(
            self._session,
            attachment=attachment,
            # The store's numbers. A client that lied about its upload produces a row that
            # disagrees with the object, and it disagrees here rather than silently forever.
            size_bytes=stored.size_bytes,
            checksum=stored.etag,
            completed_at=dt.datetime.now(dt.UTC),
        )
        self._audit(
            action=Action.ATTACH,
            resource_type=ResourceType.EVENT,
            resource_id=event.id,
            before=before,
            after=_attachment_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "EventAttachmentCompleted",
            event.id,
            {
                "event_id": str(event.id),
                "attachment_id": str(updated.id),
                "size_bytes": updated.size_bytes,
                "checksum": updated.checksum,
            },
        )
        return updated

    def content_url(self, command: RequestAttachmentContent) -> AttachmentContent:
        """A download URL, issued only after the Event's own READ authorization has passed."""
        event = self._load_event(command.event_id)
        self._authorize(
            Action.READ,
            authorization.event_relations(event, actor_person_id=self._actor_person_id),
            event.id,
        )
        attachment = self._load_attachment(event, command.attachment_id)
        if attachment.status != AttachmentStatus.AVAILABLE.value:
            raise DomainRuleViolation(
                "BR-E-14", f"attachment is {attachment.status}, not available for download"
            )
        return AttachmentContent(
            attachment=attachment,
            download_url=self._ctx.object_store.presigned_get(
                attachment.object_key, expires_in=DOWNLOAD_WINDOW
            ),
            expires_at=dt.datetime.now(dt.UTC) + DOWNLOAD_WINDOW,
        )

    def _load_attachment(self, event: Event, attachment_id: uuid.UUID) -> EventAttachment:
        attachment = repository.get_attachment(
            self._session, org_id=self._org_id, attachment_id=attachment_id
        )
        # An attachment belonging to a different Event is not this Event's business, and saying so
        # with a 404 rather than a 403 avoids confirming it exists to somebody who cannot reach it.
        if attachment is None or attachment.event_id != event.id:
            raise EntityNotFound("event_attachment", attachment_id)
        return attachment


__all__ = [
    "AttachmentContent",
    "AttachmentService",
    "AttachmentTicket",
    "CaptureResult",
    "CaptureService",
    "EventType",
    "ServiceContext",
]
