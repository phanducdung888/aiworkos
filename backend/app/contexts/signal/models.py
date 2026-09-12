"""Signal/Capture persistence models. Mirrors migration 0009, which is authoritative.

`Event` carries no `updated_at`. Almost every row in this system has one and this one must not: the
column would imply the record can be revised, and ADR-0038's trigger makes sure it cannot. What can
change is a small set of pipeline-owned fields, and none of them is "when was this last edited".
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base

_TS = TIMESTAMP(timezone=True)


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="event_org_id_unique"),
        ForeignKeyConstraint(
            ["org_id", "revision_of_event_id"],
            ["event.org_id", "event.id"],
            name="event_revision_same_org",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    origin_domain_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: BR-E-02. Set when this Event corrects one that arrived under the same external reference.
    revision_of_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str | None] = mapped_column(Text)
    #: BR-E-12. Recorded verbatim and never overwritten by resolution.
    sender_external_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    raw_payload_uri: Mapped[str | None] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(Text, server_default="normal", nullable=False)
    participant_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    processing_status: Mapped[str] = mapped_column(
        Text, server_default="received", nullable=False
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    retention_expires_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    #: BR-E-13. Who wrote it down, which is not a claim about who authored the content.
    captured_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


class EventParticipant(Base):
    __tablename__ = "event_participant"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "event_id"], ["event.org_id", "event.id"], name="event_participant_event_fk"
        ),
        ForeignKeyConstraint(
            ["org_id", "person_id"],
            ["person.org_id", "person.id"],
            name="event_participant_person_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    external_handle: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    match_confidence: Mapped[int] = mapped_column(SmallInteger, server_default="0", nullable=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


class EventAttachment(Base):
    __tablename__ = "event_attachment"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="event_attachment_org_id_unique"),
        ForeignKeyConstraint(
            ["org_id", "event_id"], ["event.org_id", "event.id"], name="event_attachment_event_fk"
        ),
        ForeignKeyConstraint(
            ["org_id", "uploaded_by_person_id"],
            ["person.org_id", "person.id"],
            name="event_attachment_person_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: Derived server-side from (org_id, event_id, id). Never accepted from a client (ADR-0039).
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: Null until completion. These are the store's numbers, not the client's.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)
    uploaded_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


class Evidence(Base):
    """The join between the world and the model (BR-E-04 to BR-E-06).

    No API reaches this table yet. It exists now so that Events captured today are citable when
    extraction lands, without a migration that has to invent provenance for rows already written.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="evidence_org_id_unique"),
        ForeignKeyConstraint(
            ["org_id", "event_id"], ["event.org_id", "event.id"], name="evidence_event_fk"
        ),
        ForeignKeyConstraint(
            ["org_id", "superseded_by_id"],
            ["evidence.org_id", "evidence.id"],
            name="evidence_superseded_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    #: BR-E-05: verbatim, or null for an attachment where `claim_summary` carries the claim instead.
    excerpt: Mapped[str | None] = mapped_column(Text)
    claim_summary: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assertion: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[int] = mapped_column(SmallInteger, server_default="0", nullable=False)
    produced_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    produced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
