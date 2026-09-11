"""Work Core domain rules.

Pure. No I/O, no ORM, no session. Everything here takes values and returns values or raises, which
is what makes it fast to test exhaustively and safe to call from anywhere.

The database enforces the same invariants where it can (migrations 0003 and 0004). That duplication
is deliberate rather than accidental: the database is the backstop that survives bulk operations and
repair scripts, and this layer is where the *reason* lives and where a caller gets an error message
worth reading.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


class DomainRuleViolation(ValueError):
    """A business rule was broken. Carries the rule id so the message is traceable to the spec."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule


# --------------------------------------------------------------------------- vocabulary


class WorkStatus(enum.StrEnum):
    PROPOSED = "proposed"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ProjectStatus(enum.StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MilestoneStatus(enum.StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    ACHIEVED = "achieved"
    MISSED = "missed"
    CANCELLED = "cancelled"


class AssignmentRole(enum.StrEnum):
    OWNER = "OWNER"
    CONTRIBUTOR = "CONTRIBUTOR"
    REVIEWER = "REVIEWER"


class AssignmentStatus(enum.StrEnum):
    ACTIVE = "active"
    ENDED = "ended"


class DependencyKind(enum.StrEnum):
    BLOCKS = "blocks"
    INFORMS = "informs"


class DependencyStatus(enum.StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    WITHDRAWN = "withdrawn"


class Source(enum.StrEnum):
    HUMAN = "human"
    AI = "ai"
    IMPORT = "import"


# --------------------------------------------------------------------------- work lifecycle

#: BR-W-03. Everything not listed is refused; there is no permissive default.
WORK_TRANSITIONS: Mapping[WorkStatus, frozenset[WorkStatus]] = {
    WorkStatus.PROPOSED: frozenset({WorkStatus.TODO, WorkStatus.REJECTED}),
    WorkStatus.TODO: frozenset({WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED}),
    WorkStatus.IN_PROGRESS: frozenset(
        {WorkStatus.BLOCKED, WorkStatus.DONE, WorkStatus.CANCELLED}
    ),
    WorkStatus.BLOCKED: frozenset({WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED}),
    WorkStatus.DONE: frozenset({WorkStatus.IN_PROGRESS}),
    WorkStatus.CANCELLED: frozenset(),
    WorkStatus.REJECTED: frozenset(),
}

PROJECT_TRANSITIONS: Mapping[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.PROPOSED: frozenset({ProjectStatus.ACTIVE, ProjectStatus.CANCELLED}),
    ProjectStatus.ACTIVE: frozenset(
        {ProjectStatus.ON_HOLD, ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.ON_HOLD: frozenset({ProjectStatus.ACTIVE, ProjectStatus.CANCELLED}),
    ProjectStatus.COMPLETED: frozenset({ProjectStatus.ACTIVE}),
    ProjectStatus.CANCELLED: frozenset(),
}

MILESTONE_TRANSITIONS: Mapping[MilestoneStatus, frozenset[MilestoneStatus]] = {
    MilestoneStatus.PLANNED: frozenset(
        {MilestoneStatus.IN_PROGRESS, MilestoneStatus.MISSED, MilestoneStatus.CANCELLED}
    ),
    MilestoneStatus.IN_PROGRESS: frozenset(
        {MilestoneStatus.ACHIEVED, MilestoneStatus.MISSED, MilestoneStatus.CANCELLED}
    ),
    MilestoneStatus.ACHIEVED: frozenset({MilestoneStatus.IN_PROGRESS}),
    MilestoneStatus.MISSED: frozenset({MilestoneStatus.ACHIEVED, MilestoneStatus.CANCELLED}),
    MilestoneStatus.CANCELLED: frozenset(),
}

OPEN_WORK_STATUSES = frozenset(
    {WorkStatus.PROPOSED, WorkStatus.TODO, WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED}
)


@dataclass(frozen=True, slots=True)
class WorkTransitionContext:
    """Facts the caller must supply for a status change. Assembled by the application service."""

    has_active_blocking_dependency: bool = False
    has_open_children: bool = False
    project_status: ProjectStatus | None = None
    blocked_reason: str | None = None


def validate_work_transition(
    current: WorkStatus, target: WorkStatus, context: WorkTransitionContext
) -> None:
    """BR-W-03 through BR-W-09. Raises on the first rule broken."""
    if current == target:
        raise DomainRuleViolation("BR-W-03", f"work is already {target}")
    allowed = WORK_TRANSITIONS[current]
    if target not in allowed:
        permitted = ", ".join(sorted(allowed)) or "nothing"
        raise DomainRuleViolation(
            "BR-W-03", f"{current} may move to {permitted}, not {target}"
        )

    if target is WorkStatus.BLOCKED and not (
        context.has_active_blocking_dependency
        or (context.blocked_reason and context.blocked_reason.strip())
    ):
        raise DomainRuleViolation(
            "BR-W-04", "blocking requires a reason or an active blocking dependency"
        )

    if target is WorkStatus.DONE:
        if context.has_active_blocking_dependency:
            raise DomainRuleViolation(
                "BR-W-05", "work cannot be completed while an active blocker remains"
            )
        if context.has_open_children:
            raise DomainRuleViolation(
                "BR-W-05", "work cannot be completed while child work is still open"
            )

    # BR-W-09. Work with no project is unconstrained here, which is the point of ADR-0029.
    startable = {ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD}
    if (
        target is WorkStatus.IN_PROGRESS
        and context.project_status is not None
        and context.project_status not in startable
    ):
        raise DomainRuleViolation(
            "BR-W-09", f"work in a {context.project_status} project cannot be started"
        )


def completed_at_for(target: WorkStatus, now: dt.datetime) -> dt.datetime | None:
    """BR-W-08. Completion stamps the clock; reopening clears it."""
    return now if target is WorkStatus.DONE else None


def validate_work_creation(
    *,
    title: str,
    source: Source,
    project_id: uuid.UUID | None,
    milestone_id: uuid.UUID | None,
) -> WorkStatus:
    """BR-W-01, BR-W-02, BR-W-07, ADR-0029. Returns the status the work should start in."""
    if not title or not title.strip():
        raise DomainRuleViolation("BR-W-01", "work requires a non-empty title")
    if milestone_id is not None and project_id is None:
        raise DomainRuleViolation(
            "BR-P-06", "work can only reference a milestone within its own project"
        )
    # Project is optional and its absence is never an error (ADR-0029, BR-W-07).
    if source is Source.AI:
        # AI-originated work exists as a Proposal until approved; on approval it is created in
        # `todo` by the approving path, never as a `proposed` work row (BR-W-02, BR-AI-16).
        raise DomainRuleViolation(
            "BR-AI-16",
            "AI-originated work is created through an approved Proposal, not directly",
        )
    return WorkStatus.TODO


def validate_work_hierarchy(
    *, work_id: uuid.UUID, parent_id: uuid.UUID | None, ancestors: Iterable[uuid.UUID]
) -> None:
    """BR-W-06. `ancestors` is the parent chain, nearest first, supplied by the repository."""
    if parent_id is None:
        return
    if parent_id == work_id:
        raise DomainRuleViolation("BR-W-06", "work cannot be its own parent")
    chain = list(ancestors)
    if work_id in chain:
        raise DomainRuleViolation("BR-W-06", "work hierarchy would contain a cycle")
    depth = len(chain) + 2  # the parent chain, the parent itself, and this work item
    if depth > 3:
        raise DomainRuleViolation(
            "BR-W-06", f"work hierarchy would be {depth} deep; the maximum is 3"
        )


# --------------------------------------------------------------------------- assignment


@dataclass(frozen=True, slots=True)
class ExistingAssignment:
    person_id: uuid.UUID
    role: AssignmentRole
    status: AssignmentStatus = AssignmentStatus.ACTIVE
    is_primary: bool = False


def validate_assignment(
    *,
    person_id: uuid.UUID,
    role: AssignmentRole,
    is_primary: bool,
    person_status: str,
    existing: Iterable[ExistingAssignment],
) -> None:
    """BR-W-11 through BR-W-16 and ADR-0032.

    Work with no assignment is valid, so there is deliberately no rule requiring one.
    """
    if person_status != "active":
        raise DomainRuleViolation(
            "BR-I-05", f"work cannot be assigned to a person with status {person_status}"
        )

    active = [a for a in existing if a.status is AssignmentStatus.ACTIVE]

    if role is AssignmentRole.OWNER and any(a.role is AssignmentRole.OWNER for a in active):
        raise DomainRuleViolation(
            "BR-W-13",
            "work already has an active OWNER; end that assignment before creating another",
        )
    if any(a.person_id == person_id and a.role is role for a in active):
        raise DomainRuleViolation(
            "BR-W-14", f"this person already holds an active {role} assignment on this work"
        )
    if is_primary and any(a.is_primary for a in active):
        raise DomainRuleViolation("BR-W-16", "work already has an active primary assignment")


def current_owner(assignments: Iterable[ExistingAssignment]) -> uuid.UUID | None:
    """The read-model rule, expressed once. There is no owner column to consult (ADR-0032)."""
    for assignment in assignments:
        if assignment.role is AssignmentRole.OWNER and assignment.status is AssignmentStatus.ACTIVE:
            return assignment.person_id
    return None


# --------------------------------------------------------------------------- dependencies


@dataclass(frozen=True, slots=True)
class Endpoint:
    type: str  # "work" | "milestone"
    id: uuid.UUID

    def __post_init__(self) -> None:
        if self.type not in {"work", "milestone"}:
            raise DomainRuleViolation(
                "BR-D-01", f"dependency endpoints are work or milestone, not {self.type}"
            )


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    blocker: Endpoint
    blocked: Endpoint
    kind: DependencyKind = DependencyKind.BLOCKS
    status: DependencyStatus = DependencyStatus.ACTIVE


def find_cycle(
    edges: Iterable[DependencyEdge], candidate: DependencyEdge
) -> list[Endpoint] | None:
    """Return the path that `candidate` would close, or None.

    Only active `blocks` edges constrain scheduling, so soft `informs` edges are excluded by design.
    Returning the path rather than a boolean is what lets the API tell a user *which* chain they
    just tried to close, which is the difference between a usable error and a mysterious one.
    """
    if candidate.kind is not DependencyKind.BLOCKS:
        return None

    graph: dict[tuple[str, uuid.UUID], list[Endpoint]] = {}
    for edge in edges:
        if edge.kind is not DependencyKind.BLOCKS or edge.status is not DependencyStatus.ACTIVE:
            continue
        graph.setdefault((edge.blocker.type, edge.blocker.id), []).append(edge.blocked)

    target = (candidate.blocker.type, candidate.blocker.id)
    stack: list[tuple[Endpoint, list[Endpoint]]] = [(candidate.blocked, [candidate.blocker])]
    seen: set[tuple[str, uuid.UUID]] = set()

    while stack:
        node, path = stack.pop()
        key = (node.type, node.id)
        if key == target:
            return [*path, node]
        if key in seen:
            continue
        seen.add(key)
        for nxt in graph.get(key, []):
            stack.append((nxt, [*path, node]))
    return None


def validate_dependency(
    *,
    candidate: DependencyEdge,
    existing: Iterable[DependencyEdge],
    blocker_org: uuid.UUID,
    blocked_org: uuid.UUID,
) -> None:
    """BR-D-01, BR-D-02, BR-D-04, BR-D-06."""
    if (candidate.blocker.type, candidate.blocker.id) == (
        candidate.blocked.type,
        candidate.blocked.id,
    ):
        raise DomainRuleViolation("BR-D-01", "a dependency endpoint cannot be both sides")
    if blocker_org != blocked_org:
        raise DomainRuleViolation(
            "BR-D-04", "cross-organization dependencies are never permitted"
        )

    edges = list(existing)
    for edge in edges:
        if edge.status is not DependencyStatus.ACTIVE:
            continue
        same = (
            (edge.blocker.type, edge.blocker.id)
            == (candidate.blocker.type, candidate.blocker.id)
            and (edge.blocked.type, edge.blocked.id)
            == (candidate.blocked.type, candidate.blocked.id)
            and edge.kind is candidate.kind
        )
        if same:
            raise DomainRuleViolation("BR-D-06", "this dependency already exists")

    cycle = find_cycle(edges, candidate)
    if cycle is not None:
        rendered = " → ".join(f"{e.type}:{str(e.id)[:8]}" for e in cycle)
        raise DomainRuleViolation("BR-D-02", f"dependency would create a cycle: {rendered}")


# --------------------------------------------------------------------------- project & milestone


@dataclass(frozen=True, slots=True)
class ProjectClosureContext:
    open_milestones: int = 0
    work_in_progress_or_blocked: int = 0


_NO_OPEN_WORK = ProjectClosureContext()


def validate_project_transition(
    current: ProjectStatus,
    target: ProjectStatus,
    context: ProjectClosureContext = _NO_OPEN_WORK,
) -> None:
    """BR-P-03 and the project state machine."""
    if current == target:
        raise DomainRuleViolation("BR-P-03", f"project is already {target}")
    if target not in PROJECT_TRANSITIONS[current]:
        permitted = ", ".join(sorted(PROJECT_TRANSITIONS[current])) or "nothing"
        raise DomainRuleViolation(
            "BR-P-03", f"{current} may move to {permitted}, not {target}"
        )
    if target is ProjectStatus.COMPLETED:
        if context.open_milestones:
            raise DomainRuleViolation(
                "BR-P-03",
                f"{context.open_milestones} milestone(s) are still open",
            )
        if context.work_in_progress_or_blocked:
            raise DomainRuleViolation(
                "BR-P-03",
                f"{context.work_in_progress_or_blocked} work item(s) are in progress or blocked",
            )


def validate_project_creation(
    *,
    name: str,
    owning_team_id: uuid.UUID | None,
    department_id: uuid.UUID | None,
    start_date: dt.date | None,
    target_date: dt.date | None,
) -> None:
    """BR-P-01, BR-P-02."""
    if not name or not name.strip():
        raise DomainRuleViolation("BR-P-01", "a project requires a name")
    if owning_team_id is None and department_id is None:
        raise DomainRuleViolation(
            "BR-P-01", "a project requires an owning team or department"
        )
    if start_date and target_date and target_date < start_date:
        raise DomainRuleViolation("BR-P-02", "target date cannot precede the start date")


def validate_milestone_transition(current: MilestoneStatus, target: MilestoneStatus) -> None:
    if current == target:
        raise DomainRuleViolation("BR-P-07", f"milestone is already {target}")
    if target not in MILESTONE_TRANSITIONS[current]:
        permitted = ", ".join(sorted(MILESTONE_TRANSITIONS[current])) or "nothing"
        raise DomainRuleViolation(
            "BR-P-07", f"{current} may move to {permitted}, not {target}"
        )


# --------------------------------------------------------------------------- reporting


class WorkPartition(enum.StrEnum):
    PROJECT = "project"
    NON_PROJECT = "non_project"


def partition_of(project_id: uuid.UUID | None) -> WorkPartition:
    """BR-RPT-01. The partition is derived from the presence of a project, nothing else."""
    return WorkPartition.NON_PROJECT if project_id is None else WorkPartition.PROJECT


@dataclass(frozen=True, slots=True)
class WorkCounts:
    """BR-RPT-04: a figure that does not say which partition it covers is ambiguous."""

    project: int = 0
    non_project: int = 0

    @property
    def total(self) -> int:
        return self.project + self.non_project


def count_by_partition(project_ids: Iterable[uuid.UUID | None]) -> WorkCounts:
    project = 0
    non_project = 0
    for project_id in project_ids:
        if project_id is None:
            non_project += 1
        else:
            project += 1
    return WorkCounts(project=project, non_project=non_project)


@dataclass(frozen=True, slots=True)
class ProjectRollup:
    """BR-RPT-02. A project rollup counts only work that belongs to the project."""

    project_id: uuid.UUID
    open_work: int = 0
    done_work: int = 0
    excluded_non_project_work: int = field(default=0)


def roll_up_project(
    project_id: uuid.UUID, work: Iterable[tuple[uuid.UUID | None, WorkStatus]]
) -> ProjectRollup:
    """Non-project work is excluded and counted separately, never folded in (BR-RPT-02)."""
    open_work = done_work = excluded = 0
    for owner_project_id, status in work:
        if owner_project_id != project_id:
            if owner_project_id is None:
                excluded += 1
            continue
        if status in OPEN_WORK_STATUSES:
            open_work += 1
        elif status is WorkStatus.DONE:
            done_work += 1
    return ProjectRollup(
        project_id=project_id,
        open_work=open_work,
        done_work=done_work,
        excluded_non_project_work=excluded,
    )
