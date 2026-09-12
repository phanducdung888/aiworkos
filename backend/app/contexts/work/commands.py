"""Inputs to the Work Core application services.

Explicit command objects rather than keyword soup, because two very different callers build them:
the HTTP API in Checkpoint 4 and the Tool Gateway in Phase 3. Both must be constrained by the same
shape, and a shape written down is one that can be diffed when it changes.

Update commands distinguish "leave this alone" from "set this to null", which a plain `None`
default cannot. `UNSET` is that distinction. It matters more here than it looks: clearing a due date
and not mentioning a due date are different intentions, and a service that cannot tell them apart
will silently erase data on a partial update.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid
from dataclasses import dataclass
from typing import Any

from app.contexts.work.domain import (
    AssignmentRole,
    DependencyKind,
    DependencyStatus,
    MilestoneStatus,
    ProjectStatus,
    Source,
    WorkStatus,
)


class _Sentinel(enum.Enum):
    UNSET = "unset"


#: "This field was not mentioned." Distinct from `None`, which means "set it to null".
UNSET = _Sentinel.UNSET

type Maybe[T] = T | _Sentinel


def value_or_none[T](value: Maybe[T]) -> T | None:
    """The caller's value, or None when they said nothing.

    For fields where "unspecified" and "null" mean the same thing to the service — a visibility the
    caller left to inheritance, say — collapsing them here keeps `_Sentinel` private to this module.
    """
    return None if isinstance(value, _Sentinel) else value


def changed_fields(command: object) -> dict[str, Any]:
    """The fields a caller actually set. Everything still `UNSET` is left untouched."""
    return {
        field.name: value
        for field in dataclasses.fields(command)  # type: ignore[arg-type]
        if not isinstance(value := getattr(command, field.name), _Sentinel)
    }


# --------------------------------------------------------------------------- project


@dataclass(frozen=True, slots=True)
class CreateProject:
    name: str
    owning_team_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    description: str | None = None
    objective: str | None = None
    lead_person_id: uuid.UUID | None = None
    sponsor_person_id: uuid.UUID | None = None
    start_date: dt.date | None = None
    target_date: dt.date | None = None
    visibility: str = "organization"
    source: Source = Source.HUMAN


@dataclass(frozen=True, slots=True)
class UpdateProject:
    project_id: uuid.UUID
    expected_version: int
    name: Maybe[str] = UNSET
    description: Maybe[str | None] = UNSET
    objective: Maybe[str | None] = UNSET
    owning_team_id: Maybe[uuid.UUID | None] = UNSET
    department_id: Maybe[uuid.UUID | None] = UNSET
    lead_person_id: Maybe[uuid.UUID | None] = UNSET
    sponsor_person_id: Maybe[uuid.UUID | None] = UNSET
    start_date: Maybe[dt.date | None] = UNSET
    target_date: Maybe[dt.date | None] = UNSET


@dataclass(frozen=True, slots=True)
class ChangeProjectStatus:
    project_id: uuid.UUID
    expected_version: int
    target: ProjectStatus


@dataclass(frozen=True, slots=True)
class ChangeProjectVisibility:
    project_id: uuid.UUID
    expected_version: int
    visibility: str


# --------------------------------------------------------------------------- milestone


@dataclass(frozen=True, slots=True)
class CreateMilestone:
    project_id: uuid.UUID
    name: str
    description: str | None = None
    acceptance_criteria: str | None = None
    target_date: dt.date | None = None
    order_index: int = 0


@dataclass(frozen=True, slots=True)
class UpdateMilestone:
    milestone_id: uuid.UUID
    expected_version: int
    name: Maybe[str] = UNSET
    description: Maybe[str | None] = UNSET
    acceptance_criteria: Maybe[str | None] = UNSET
    target_date: Maybe[dt.date | None] = UNSET
    order_index: Maybe[int] = UNSET


@dataclass(frozen=True, slots=True)
class ChangeMilestoneStatus:
    milestone_id: uuid.UUID
    expected_version: int
    target: MilestoneStatus


# --------------------------------------------------------------------------- work


@dataclass(frozen=True, slots=True)
class CreateWork:
    title: str
    # Every one of these is optional, and that is the product decision, not an oversight
    # (ADR-0029, BR-W-07, BR-W-15).
    project_id: uuid.UUID | None = None
    milestone_id: uuid.UUID | None = None
    parent_work_id: uuid.UUID | None = None
    description: str | None = None
    type: str = "task"
    priority: str = "normal"
    due_date: dt.date | None = None
    #: UNSET, not "team". With a Project the level is inherited, and inheritance is only possible if
    #: the service can tell "the caller said nothing" from "the caller said team" (BR-W-19).
    visibility: Maybe[str] = UNSET
    source: Source = Source.HUMAN


@dataclass(frozen=True, slots=True)
class UpdateWork:
    work_id: uuid.UUID
    expected_version: int
    title: Maybe[str] = UNSET
    description: Maybe[str | None] = UNSET
    type: Maybe[str] = UNSET
    priority: Maybe[str] = UNSET
    due_date: Maybe[dt.date | None] = UNSET
    project_id: Maybe[uuid.UUID | None] = UNSET
    milestone_id: Maybe[uuid.UUID | None] = UNSET
    parent_work_id: Maybe[uuid.UUID | None] = UNSET


@dataclass(frozen=True, slots=True)
class ChangeWorkStatus:
    work_id: uuid.UUID
    expected_version: int
    target: WorkStatus
    #: Required when moving to `blocked` unless an active blocking dependency exists (BR-W-04).
    blocked_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeWorkVisibility:
    work_id: uuid.UUID
    expected_version: int
    visibility: str


# --------------------------------------------------------------------------- assignment


@dataclass(frozen=True, slots=True)
class AssignWork:
    work_id: uuid.UUID
    person_id: uuid.UUID
    role: AssignmentRole = AssignmentRole.CONTRIBUTOR
    is_primary: bool = False
    source: Source = Source.HUMAN


@dataclass(frozen=True, slots=True)
class EndAssignment:
    assignment_id: uuid.UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class SetOwner:
    """Make this person the active OWNER, whoever holds it now.

    One command for both cases because the caller's intention is one thing: this person owns it. Who
    held it before changes which permission is required, not what was asked for, and the service is
    the only layer that knows the current state well enough to decide.
    """

    work_id: uuid.UUID
    person_id: uuid.UUID
    is_primary: bool = False
    source: Source = Source.HUMAN


@dataclass(frozen=True, slots=True)
class ReassignOwner:
    """Move ownership. A distinct command because it is a distinct permission (ADR-0032).

    Editing work you own and deciding who owns it are different powers, so `WORK_ASSIGNMENT.ASSIGN`
    and `WORK_ASSIGNMENT.REASSIGN` are different matrix rows and this is the call that needs the
    second one.
    """

    work_id: uuid.UUID
    person_id: uuid.UUID
    is_primary: bool = False
    source: Source = Source.HUMAN


# --------------------------------------------------------------------------- dependency


@dataclass(frozen=True, slots=True)
class CreateDependency:
    blocker_type: str
    blocker_id: uuid.UUID
    blocked_type: str
    blocked_id: uuid.UUID
    kind: DependencyKind = DependencyKind.BLOCKS
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeDependencyStatus:
    dependency_id: uuid.UUID
    expected_version: int
    target: DependencyStatus
