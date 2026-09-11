"""Identity & Organization persistence models.

These mirror migrations 0001 and 0002. The migration is authoritative; these exist so that queries
are typed and so that `tests/integration/test_migrations.py` can prove the two have not drifted.

Nothing here contains business logic. Invariants live in the database (constraints, triggers, RLS)
and in the domain layer that arrives with the application services in a later checkpoint.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
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


class Organization(_Timestamped, Base):
    __tablename__ = "organization"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, server_default="UTC", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)
    ai_enabled: Mapped[bool] = mapped_column(server_default="false", nullable=False)
    autonomy_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    retention_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="organization_status"),
        UniqueConstraint("slug", name="uq_organization_slug"),
    )


class Person(_Timestamped, Base):
    __tablename__ = "person"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    keycloak_subject: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)
    timezone: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive', 'departed')", name="person_status"
        ),
        UniqueConstraint("id", "org_id", name="uq_person_id_org_id"),
        UniqueConstraint(
            "org_id", "keycloak_subject", name="uq_person_org_id_keycloak_subject"
        ),
        Index("ix_person_org_id_status", "org_id", "status"),
    )


class OrganizationMembership(_Timestamped, Base):
    __tablename__ = "organization_membership"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)
    joined_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    left_at: Mapped[dt.datetime | None] = mapped_column(_TS)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'ended')",
            name="organization_membership_status",
        ),
        UniqueConstraint("id", "org_id", name="uq_organization_membership_id_org_id"),
        UniqueConstraint(
            "org_id", "person_id", name="uq_organization_membership_org_id_person_id"
        ),
        ForeignKeyConstraint(
            ["person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_organization_membership_person_id_org_id",
        ),
    )


class Department(_Timestamped, Base):
    __tablename__ = "department"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    parent_department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    lead_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="department_status"),
        CheckConstraint("parent_department_id <> id", name="department_not_own_parent"),
        UniqueConstraint("id", "org_id", name="uq_department_id_org_id"),
        ForeignKeyConstraint(
            ["parent_department_id", "org_id"],
            ["department.id", "department.org_id"],
            name="fk_department_parent_department_id_org_id",
        ),
        ForeignKeyConstraint(
            ["lead_person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_department_lead_person_id_org_id",
        ),
        Index(
            "ix_department_org_id_parent_department_id", "org_id", "parent_department_id"
        ),
    )


class Team(_Timestamped, Base):
    __tablename__ = "team"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    lead_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="team_status"),
        UniqueConstraint("id", "org_id", name="uq_team_id_org_id"),
        ForeignKeyConstraint(
            ["department_id", "org_id"],
            ["department.id", "department.org_id"],
            name="fk_team_department_id_org_id",
        ),
        ForeignKeyConstraint(
            ["lead_person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_team_lead_person_id_org_id",
        ),
        Index("ix_team_org_id_department_id", "org_id", "department_id"),
    )


class TeamMembership(_Timestamped, Base):
    __tablename__ = "team_membership"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(Text, server_default="member", nullable=False)
    valid_from: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    valid_to: Mapped[dt.datetime | None] = mapped_column(_TS)

    __table_args__ = (
        CheckConstraint(
            "role IN ('lead', 'member', 'guest')", name="team_membership_role"
        ),
        UniqueConstraint("id", "org_id", name="uq_team_membership_id_org_id"),
        ForeignKeyConstraint(
            ["team_id", "org_id"],
            ["team.id", "team.org_id"],
            name="fk_team_membership_team_id_org_id",
        ),
        ForeignKeyConstraint(
            ["person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_team_membership_person_id_org_id",
        ),
    )


class RoleAssignment(_Timestamped, Base):
    __tablename__ = "role_assignment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(
        Text, server_default="organization", nullable=False
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    granted_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    granted_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(_TS)

    __table_args__ = (
        CheckConstraint(
            "role IN ('org_admin', 'department_lead', 'team_lead', "
            "'member', 'viewer', 'auditor', 'executive')",
            name="role_assignment_role",
        ),
        CheckConstraint(
            "scope_type IN ('organization', 'department', 'team', 'project')",
            name="role_assignment_scope_type",
        ),
        CheckConstraint(
            "scope_type = 'organization' OR scope_id IS NOT NULL",
            name="role_assignment_scope_id_required",
        ),
        UniqueConstraint("id", "org_id", name="uq_role_assignment_id_org_id"),
        ForeignKeyConstraint(
            ["person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_role_assignment_person_id_org_id",
        ),
    )


class ExternalIdentity(_Timestamped, Base):
    __tablename__ = "external_identity"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(
        SmallInteger, server_default="0", nullable=False
    )
    confirmed_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(_TS)

    __table_args__ = (
        CheckConstraint(
            "confidence BETWEEN 0 AND 100", name="external_identity_confidence"
        ),
        UniqueConstraint("id", "org_id", name="uq_external_identity_id_org_id"),
        UniqueConstraint(
            "org_id",
            "source_system",
            "external_id",
            name="uq_external_identity_org_id_source_system_external_id",
        ),
        ForeignKeyConstraint(
            ["person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_external_identity_person_id_org_id",
        ),
        ForeignKeyConstraint(
            ["confirmed_by_person_id", "org_id"],
            ["person.id", "person.org_id"],
            name="fk_external_identity_confirmed_by_person_id_org_id",
        ),
    )
