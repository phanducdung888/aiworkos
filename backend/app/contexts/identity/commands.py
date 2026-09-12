"""Inputs to the Identity application services.

Same shape as the Work Core's commands, including `UNSET`: on a partial update, "leave the email
alone" and "clear the email" are different intentions and a service that cannot tell them apart
erases data on every PATCH.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from app.contexts.identity.domain import (
    MembershipStatus,
    PersonStatus,
    ScopeType,
    TeamRole,
    UnitStatus,
)
from app.platform.partial import UNSET, Maybe

__all__ = [
    "UNSET",
    "AddTeamMember",
    "ChangeMembershipStatus",
    "ChangePersonStatus",
    "ChangeUnitStatus",
    "ConfirmExternalIdentity",
    "CreateDepartment",
    "CreateExternalIdentity",
    "CreateMembership",
    "CreatePerson",
    "CreateTeam",
    "EndTeamMembership",
    "GrantRole",
    "Maybe",
    "RevokeRole",
    "UpdateDepartment",
    "UpdateExternalIdentity",
    "UpdateOrganization",
    "UpdatePerson",
    "UpdateTeam",
]


@dataclass(frozen=True, slots=True)
class UpdateOrganization:
    expected_version: int
    name: Maybe[str] = UNSET
    timezone: Maybe[str] = UNSET


@dataclass(frozen=True, slots=True)
class CreatePerson:
    display_name: str
    email: str | None = None
    #: ADR-0036. Set when the Person is somebody who will sign in; left null for somebody who is
    #: only ever referenced (BR-I-04).
    keycloak_subject: str | None = None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class UpdatePerson:
    person_id: uuid.UUID
    expected_version: int
    display_name: Maybe[str] = UNSET
    email: Maybe[str | None] = UNSET
    keycloak_subject: Maybe[str | None] = UNSET
    timezone: Maybe[str | None] = UNSET


@dataclass(frozen=True, slots=True)
class ChangePersonStatus:
    person_id: uuid.UUID
    expected_version: int
    target: PersonStatus


@dataclass(frozen=True, slots=True)
class CreateDepartment:
    name: str
    parent_department_id: uuid.UUID | None = None
    lead_person_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class UpdateDepartment:
    department_id: uuid.UUID
    expected_version: int
    name: Maybe[str] = UNSET
    parent_department_id: Maybe[uuid.UUID | None] = UNSET
    lead_person_id: Maybe[uuid.UUID | None] = UNSET


@dataclass(frozen=True, slots=True)
class CreateTeam:
    name: str
    department_id: uuid.UUID | None = None
    lead_person_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class UpdateTeam:
    team_id: uuid.UUID
    expected_version: int
    name: Maybe[str] = UNSET
    department_id: Maybe[uuid.UUID | None] = UNSET
    lead_person_id: Maybe[uuid.UUID | None] = UNSET


@dataclass(frozen=True, slots=True)
class ChangeUnitStatus:
    """Archiving a department or a team. They are never deleted (BR-G-04)."""

    entity_id: uuid.UUID
    expected_version: int
    target: UnitStatus


@dataclass(frozen=True, slots=True)
class AddTeamMember:
    team_id: uuid.UUID
    person_id: uuid.UUID
    role: TeamRole = TeamRole.MEMBER
    valid_from: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class EndTeamMembership:
    membership_id: uuid.UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class CreateMembership:
    person_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ChangeMembershipStatus:
    membership_id: uuid.UUID
    expected_version: int
    target: MembershipStatus


@dataclass(frozen=True, slots=True)
class GrantRole:
    person_id: uuid.UUID
    role: str
    scope_type: ScopeType = ScopeType.ORGANIZATION
    scope_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class RevokeRole:
    assignment_id: uuid.UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class CreateExternalIdentity:
    person_id: uuid.UUID
    source_system: str
    external_id: str
    handle: str | None = None
    #: Created unconfirmed regardless of what the caller claims about confidence (ADR-0037).
    confidence: int = 0


@dataclass(frozen=True, slots=True)
class UpdateExternalIdentity:
    identity_id: uuid.UUID
    expected_version: int
    handle: Maybe[str | None] = UNSET
    confidence: Maybe[int] = UNSET


@dataclass(frozen=True, slots=True)
class ConfirmExternalIdentity:
    """ADR-0037. Confirming records who did it and when — the provenance BR-I-06 rests on."""

    identity_id: uuid.UUID
    expected_version: int
