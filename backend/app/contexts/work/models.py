"""Work Core persistence models.

Mirrors migrations 0003 and 0004. The migration is authoritative; these exist for typed queries and
so the drift test can prove the two agree.

Note what is absent from `Work`: there is no `assignee_person_id`, no `owner_person_id` and no
collaborator array. Assignment lives in `WorkAssignment` and only there (ADR-0032), and
`test_work_has_no_assignee_column` fails if that ever changes.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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


class _Timestamped:
    created_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now(), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


class Project(_Timestamped, Base):
    __tablename__ = "project"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(Text)
    owning_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lead_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sponsor_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, server_default="proposed", nullable=False)
    start_date: Mapped[dt.date | None] = mapped_column(Date)
    target_date: Mapped[dt.date | None] = mapped_column(Date)
    actual_end_date: Mapped[dt.date | None] = mapped_column(Date)
    visibility: Mapped[str] = mapped_column(Text, server_default="organization", nullable=False)
    source: Mapped[str] = mapped_column(Text, server_default="human", nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'active', 'on_hold', 'completed', 'cancelled')",
            name="project_status",
        ),
        CheckConstraint(
            "visibility IN ('organization', 'department', 'team', 'restricted')",
            name="project_visibility",
        ),
        CheckConstraint("source IN ('human', 'ai', 'import')", name="project_source"),
        CheckConstraint(
            "start_date IS NULL OR target_date IS NULL OR target_date >= start_date",
            name="project_dates",
        ),
        CheckConstraint(
            "owning_team_id IS NOT NULL OR department_id IS NOT NULL",
            name="project_owner_required",
        ),
        UniqueConstraint("id", "org_id", name="uq_project_id_org_id"),
        ForeignKeyConstraint(
            ["owning_team_id", "org_id"],
            ["team.id", "team.org_id"],
            name="fk_project_owning_team_id_org_id",
        ),
        ForeignKeyConstraint(
            ["department_id", "org_id"],
            ["department.id", "department.org_id"],
            name="fk_project_department_id_org_id",
        ),
        ForeignKeyConstraint(
            ["lead_person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_project_lead_person_id_org_id",
        ),
        ForeignKeyConstraint(
            ["sponsor_person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_project_sponsor_person_id_org_id",
        ),
        Index("ix_project_org_id_status", "org_id", "status"),
    )


class Milestone(_Timestamped, Base):
    __tablename__ = "milestone"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[dt.date | None] = mapped_column(Date)
    actual_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default="planned", nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'in_progress', 'achieved', 'missed', 'cancelled')",
            name="milestone_status",
        ),
        UniqueConstraint("id", "org_id", name="uq_milestone_id_org_id"),
        UniqueConstraint(
            "id", "project_id", "org_id", name="uq_milestone_id_project_id_org_id"
        ),
        ForeignKeyConstraint(
            ["project_id", "org_id"],
            ["project.id", "project.org_id"],
            name="fk_milestone_project_id_org_id",
        ),
        Index("ix_milestone_org_id_project_id", "org_id", "project_id"),
    )


class Work(_Timestamped, Base):
    __tablename__ = "work"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    # Optional by decision (ADR-0029). Work without a project is first-class.
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    milestone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    parent_work_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text, server_default="task", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="todo", nullable=False)
    priority: Mapped[str] = mapped_column(Text, server_default="normal", nullable=False)
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    completed_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    confidence: Mapped[int | None] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(Text, server_default="human", nullable=False)
    origin_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_signal_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    visibility: Mapped[str] = mapped_column(Text, server_default="team", nullable=False)

    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="work_title_not_blank"),
        CheckConstraint(
            "type IN ('deliverable', 'task', 'activity', 'investigation')", name="work_type"
        ),
        CheckConstraint(
            "status IN ('proposed', 'todo', 'in_progress', 'blocked', "
            "'done', 'cancelled', 'rejected')",
            name="work_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'critical')", name="work_priority"
        ),
        CheckConstraint(
            "visibility IN ('organization', 'department', 'team', 'restricted')",
            name="work_visibility",
        ),
        CheckConstraint("source IN ('human', 'ai', 'import')", name="work_source"),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 100", name="work_confidence"
        ),
        CheckConstraint("parent_work_id <> id", name="work_not_own_parent"),
        CheckConstraint(
            "milestone_id IS NULL OR project_id IS NOT NULL",
            name="work_milestone_requires_project",
        ),
        CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)",
            name="work_completed_at_matches_status",
        ),
        UniqueConstraint("id", "org_id", name="uq_work_id_org_id"),
        ForeignKeyConstraint(
            ["project_id", "org_id"],
            ["project.id", "project.org_id"],
            name="fk_work_project_id_org_id",
        ),
        ForeignKeyConstraint(
            ["milestone_id", "project_id", "org_id"],
            ["milestone.id", "milestone.project_id", "milestone.org_id"],
            name="fk_work_milestone_id_project_id_org_id",
        ),
        ForeignKeyConstraint(
            ["parent_work_id", "org_id"],
            ["work.id", "work.org_id"],
            name="fk_work_parent_work_id_org_id",
        ),
        Index("ix_work_org_id_status", "org_id", "status"),
        Index("ix_work_org_id_project_id", "org_id", "project_id"),
    )


class WorkAssignment(_Timestamped, Base):
    __tablename__ = "work_assignment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    work_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)
    assigned_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    assigned_by_actor: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    ended_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    source: Mapped[str] = mapped_column(Text, server_default="human", nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('OWNER', 'CONTRIBUTOR', 'REVIEWER')", name="work_assignment_role"
        ),
        CheckConstraint("status IN ('active', 'ended')", name="work_assignment_status"),
        CheckConstraint(
            "(status = 'ended') = (ended_at IS NOT NULL)",
            name="work_assignment_ended_at_matches_status",
        ),
        UniqueConstraint("id", "org_id", name="uq_work_assignment_id_org_id"),
        ForeignKeyConstraint(
            ["work_id", "org_id"],
            ["work.id", "work.org_id"],
            name="fk_work_assignment_work_id_org_id",
        ),
        ForeignKeyConstraint(
            ["person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_work_assignment_person_id_org_id",
        ),
    )


class Dependency(_Timestamped, Base):
    __tablename__ = "dependency"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    blocker_type: Mapped[str] = mapped_column(Text, nullable=False)
    blocker_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    blocked_type: Mapped[str] = mapped_column(Text, nullable=False)
    blocked_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, server_default="blocks", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "blocker_type IN ('work', 'milestone')", name="dependency_blocker_type"
        ),
        CheckConstraint(
            "blocked_type IN ('work', 'milestone')", name="dependency_blocked_type"
        ),
        CheckConstraint("kind IN ('blocks', 'informs')", name="dependency_kind"),
        CheckConstraint(
            "status IN ('active', 'resolved', 'withdrawn')", name="dependency_status"
        ),
        CheckConstraint(
            "blocker_type <> blocked_type OR blocker_id <> blocked_id",
            name="dependency_distinct_endpoints",
        ),
        UniqueConstraint("id", "org_id", name="uq_dependency_id_org_id"),
    )
