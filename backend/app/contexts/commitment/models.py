"""Commitment persistence. Mirrors migration 0010, which is authoritative."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base

_TS = TIMESTAMP(timezone=True)


class Commitment(Base):
    __tablename__ = "commitment"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="commitment_org_id_unique"),
        ForeignKeyConstraint(
            ["org_id", "committed_by_person_id"],
            ["person.org_id", "person.id"],
            name="commitment_committer_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "committed_to_person_id"],
            ["person.org_id", "person.id"],
            name="commitment_recipient_person_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "committed_to_team_id"],
            ["team.org_id", "team.id"],
            name="commitment_recipient_team_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "fulfilling_work_id"],
            ["work.org_id", "work.id"],
            name="commitment_work_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "project_id"], ["project.org_id", "project.id"], name="commitment_project_fk"
        ),
        ForeignKeyConstraint(
            ["org_id", "origin_event_id"], ["event.org_id", "event.id"], name="commitment_event_fk"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    committed_by_person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    committed_to_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    committed_to_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    due_precision: Mapped[str] = mapped_column(Text, server_default="vague", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="captured", nullable=False)
    #: BR-C-08. A link, not a merge: completing the Work proposes fulfilment, never forces it.
    fulfilling_work_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: The Event where the promise was made. Null for one a person typed in directly (BR-C-03).
    origin_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confidence: Mapped[int] = mapped_column(SmallInteger, server_default="0", nullable=False)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    #: BR-C-07. Kept on the row as well as in audit so a reader can see the promise moved without
    #: having to reconstruct it from the trail.
    previous_due_date: Mapped[dt.date | None] = mapped_column(Date)
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
