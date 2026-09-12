"""Work Core persistence.

The only module in this context that talks to PostgreSQL. Services orchestrate, the domain decides,
this reads and writes rows. Nothing here makes a business decision: every function either answers a
question a service asked or applies a change a service already validated.

Two things are worth knowing before reading further.

Reads are scoped twice, by `org_id` in the predicate and by RLS on the connection. That is not
belt-and-braces about typos; it is BR-G-01a, which says neither control may become the excuse for
dropping the other.

Writes go through a version guard. `UPDATE ... WHERE version = :expected` makes optimistic
concurrency a property of the statement rather than of a read-then-write sequence, which is the
difference between refusing a stale write and usually refusing one (BR-G-06).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, Update, case, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.contexts.work.domain import (
    OPEN_WORK_STATUSES,
    VISIBILITY_ORDER,
    AssignmentRole,
    AssignmentStatus,
    DependencyEdge,
    DependencyKind,
    DependencyStatus,
    Endpoint,
    ExistingAssignment,
    MilestoneStatus,
    WorkStatus,
)
from app.contexts.work.models import (
    Dependency,
    Milestone,
    Project,
    Work,
    WorkAssignment,
)
from app.platform.concurrency import StaleVersionError
from app.platform.ids import uuid7

_OPEN_WORK = tuple(s.value for s in OPEN_WORK_STATUSES)
_OPEN_MILESTONE = (MilestoneStatus.PLANNED.value, MilestoneStatus.IN_PROGRESS.value)


def _apply(
    session: Session, stmt: Update, *, resource: str, entity_id: uuid.UUID, expected: int
) -> None:
    """Run a version-guarded UPDATE. No rows touched means somebody else got there first."""
    result = cast("CursorResult[Any]", session.execute(stmt))
    if result.rowcount == 0:
        raise StaleVersionError(resource, entity_id, expected)


# --------------------------------------------------------------------------- project


def get_project(
    session: Session, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> Project | None:
    return session.execute(
        select(Project)
        .where(Project.id == project_id, Project.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_project(session: Session, *, org_id: uuid.UUID, **values: Any) -> Project:
    project = Project(id=uuid7(), org_id=org_id, **values)
    session.add(project)
    session.flush()
    return project


def update_project(
    session: Session,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> Project:
    _apply(
        session,
        update(Project)
        .where(
            Project.id == project_id,
            Project.org_id == org_id,
            Project.version == expected_version,
        )
        .values(**values, version=Project.version + 1, updated_at=func.now()),
        resource="project",
        entity_id=project_id,
        expected=expected_version,
    )
    project = get_project(session, org_id=org_id, project_id=project_id)
    assert project is not None  # noqa: S101 - the guarded update above just matched it
    return project


def count_open_milestones(
    session: Session, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(Milestone)
            .where(
                Milestone.org_id == org_id,
                Milestone.project_id == project_id,
                Milestone.status.in_(_OPEN_MILESTONE),
            )
        ).scalar_one()
    )


def count_work_in_progress_or_blocked(
    session: Session, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(Work)
            .where(
                Work.org_id == org_id,
                Work.project_id == project_id,
                Work.status.in_((WorkStatus.IN_PROGRESS.value, WorkStatus.BLOCKED.value)),
            )
        ).scalar_one()
    )


def work_wider_than(
    session: Session, *, org_id: uuid.UUID, project_id: uuid.UUID, visibility: str
) -> list[Work]:
    """BR-W-19. Work of this project whose visibility is wider than `visibility`.

    Ranked in SQL rather than fetched and filtered, so a project with thousands of Work items does
    not load all of them to narrow four.
    """
    levels = [level.value for level in VISIBILITY_ORDER]
    # A CASE rather than a Postgres array lookup: the ordering is a domain fact (VISIBILITY_ORDER),
    # and expressing it as a mapping keeps the SQL saying the same thing the domain does.
    rank = case({level: index for index, level in enumerate(levels)}, value=Work.visibility)
    limit = levels.index(visibility)
    return list(
        session.execute(
            select(Work)
            .where(
                Work.org_id == org_id,
                Work.project_id == project_id,
                rank < limit,
            )
            .order_by(Work.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def open_milestones_of_project(
    session: Session, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Milestone]:
    """BR-P-04. The milestones a project cancellation has to take with it."""
    return list(
        session.execute(
            select(Milestone)
            .where(
                Milestone.org_id == org_id,
                Milestone.project_id == project_id,
                Milestone.status.in_(_OPEN_MILESTONE),
            )
            .order_by(Milestone.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def open_work_of_project(
    session: Session, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> list[Work]:
    """BR-P-04. Cancellable work only: `proposed` work is rejected, not cancelled (BR-W-03)."""
    return list(
        session.execute(
            select(Work)
            .where(
                Work.org_id == org_id,
                Work.project_id == project_id,
                Work.status.in_(_OPEN_WORK),
            )
            .order_by(Work.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


# --------------------------------------------------------------------------- milestone


def get_milestone(
    session: Session, *, org_id: uuid.UUID, milestone_id: uuid.UUID
) -> Milestone | None:
    return session.execute(
        select(Milestone)
        .where(Milestone.id == milestone_id, Milestone.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_milestone(session: Session, *, org_id: uuid.UUID, **values: Any) -> Milestone:
    milestone = Milestone(id=uuid7(), org_id=org_id, **values)
    session.add(milestone)
    session.flush()
    return milestone


def update_milestone(
    session: Session,
    *,
    org_id: uuid.UUID,
    milestone_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> Milestone:
    _apply(
        session,
        update(Milestone)
        .where(
            Milestone.id == milestone_id,
            Milestone.org_id == org_id,
            Milestone.version == expected_version,
        )
        .values(**values, version=Milestone.version + 1, updated_at=func.now()),
        resource="milestone",
        entity_id=milestone_id,
        expected=expected_version,
    )
    milestone = get_milestone(session, org_id=org_id, milestone_id=milestone_id)
    assert milestone is not None  # noqa: S101
    return milestone


# --------------------------------------------------------------------------- work


def get_work(session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID) -> Work | None:
    return session.execute(
        select(Work)
        .where(Work.id == work_id, Work.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_work(session: Session, *, org_id: uuid.UUID, **values: Any) -> Work:
    work = Work(id=uuid7(), org_id=org_id, **values)
    session.add(work)
    session.flush()
    return work


def update_work(
    session: Session,
    *,
    org_id: uuid.UUID,
    work_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> Work:
    _apply(
        session,
        update(Work)
        .where(
            Work.id == work_id,
            Work.org_id == org_id,
            Work.version == expected_version,
        )
        .values(**values, version=Work.version + 1, updated_at=func.now()),
        resource="work",
        entity_id=work_id,
        expected=expected_version,
    )
    work = get_work(session, org_id=org_id, work_id=work_id)
    assert work is not None  # noqa: S101
    return work


_ANCESTORS = text(
    """
    WITH RECURSIVE chain AS (
        SELECT parent_work_id AS id, 1 AS depth FROM work
        WHERE id = :work_id AND org_id = :org_id AND parent_work_id IS NOT NULL
        UNION ALL
        SELECT w.parent_work_id, chain.depth + 1 FROM work w
        JOIN chain ON w.id = chain.id
        WHERE w.org_id = :org_id AND w.parent_work_id IS NOT NULL AND chain.depth < 10
    )
    SELECT id FROM chain ORDER BY depth
    """
)


def ancestor_ids(
    session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID
) -> list[uuid.UUID]:
    """The parent chain above `work_id`, nearest first, as BR-W-06 wants it.

    The depth guard is not the rule — BR-W-06 is, and the domain enforces it. It is here so a
    hierarchy already corrupted by a repair script cannot spin this query forever while the
    validation that would have rejected it waits for a result.
    """
    rows = session.execute(_ANCESTORS, {"work_id": work_id, "org_id": org_id}).scalars()
    return list(rows)


def has_open_children(session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID) -> bool:
    """BR-W-05."""
    return (
        session.execute(
            select(func.count())
            .select_from(Work)
            .where(
                Work.org_id == org_id,
                Work.parent_work_id == work_id,
                Work.status.in_(_OPEN_WORK),
            )
        ).scalar_one()
        > 0
    )


def has_active_blocking_dependency(
    session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID
) -> bool:
    """BR-W-04 and BR-W-05: is something actively blocking this work right now."""
    return (
        session.execute(
            select(func.count())
            .select_from(Dependency)
            .where(
                Dependency.org_id == org_id,
                Dependency.blocked_type == "work",
                Dependency.blocked_id == work_id,
                Dependency.kind == DependencyKind.BLOCKS.value,
                Dependency.status == DependencyStatus.ACTIVE.value,
            )
        ).scalar_one()
        > 0
    )


# --------------------------------------------------------------------------- assignment


def get_assignment(
    session: Session, *, org_id: uuid.UUID, assignment_id: uuid.UUID
) -> WorkAssignment | None:
    return session.execute(
        select(WorkAssignment)
        .where(WorkAssignment.id == assignment_id, WorkAssignment.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def assignments_for_work(
    session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID
) -> list[WorkAssignment]:
    """Every assignment, ended ones included. History is never deleted (BR-W-14)."""
    return list(
        session.execute(
            select(WorkAssignment)
            .where(WorkAssignment.org_id == org_id, WorkAssignment.work_id == work_id)
            .order_by(WorkAssignment.assigned_at)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def existing_assignments(
    session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID
) -> list[ExistingAssignment]:
    """The same rows, as the value objects the domain validates against."""
    return [
        ExistingAssignment(
            person_id=row.person_id,
            role=AssignmentRole(row.role),
            status=AssignmentStatus(row.status),
            is_primary=row.is_primary,
        )
        for row in assignments_for_work(session, org_id=org_id, work_id=work_id)
    ]


def insert_assignment(
    session: Session, *, org_id: uuid.UUID, **values: Any
) -> WorkAssignment:
    assignment = WorkAssignment(id=uuid7(), org_id=org_id, **values)
    session.add(assignment)
    session.flush()
    return assignment


def end_assignment(
    session: Session,
    *,
    org_id: uuid.UUID,
    assignment_id: uuid.UUID,
    expected_version: int,
    ended_at: dt.datetime,
) -> WorkAssignment:
    """BR-W-14. Ending is an update to two columns, never a delete."""
    _apply(
        session,
        update(WorkAssignment)
        .where(
            WorkAssignment.id == assignment_id,
            WorkAssignment.org_id == org_id,
            WorkAssignment.version == expected_version,
        )
        .values(
            status=AssignmentStatus.ENDED.value,
            ended_at=ended_at,
            version=WorkAssignment.version + 1,
            updated_at=func.now(),
        ),
        resource="work_assignment",
        entity_id=assignment_id,
        expected=expected_version,
    )
    assignment = get_assignment(session, org_id=org_id, assignment_id=assignment_id)
    assert assignment is not None  # noqa: S101
    return assignment


def active_owner_person_id(
    session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID
) -> uuid.UUID | None:
    """Reads the view, not the table: there is no owner column to consult (ADR-0032)."""
    return session.execute(
        text(
            "SELECT person_id FROM work_current_owner "
            "WHERE work_id = :work_id AND org_id = :org_id"
        ),
        {"work_id": work_id, "org_id": org_id},
    ).scalar()


def work_ids_assigned_to(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """Work this person holds any active assignment on, in any role."""
    rows = session.execute(
        select(WorkAssignment.work_id).where(
            WorkAssignment.org_id == org_id,
            WorkAssignment.person_id == person_id,
            WorkAssignment.status == AssignmentStatus.ACTIVE.value,
        )
    ).scalars()
    return frozenset(rows)


# --------------------------------------------------------------------------- dependency


def get_dependency(
    session: Session, *, org_id: uuid.UUID, dependency_id: uuid.UUID
) -> Dependency | None:
    return session.execute(
        select(Dependency)
        .where(Dependency.id == dependency_id, Dependency.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def dependency_edges(session: Session, *, org_id: uuid.UUID) -> list[DependencyEdge]:
    """The organization's dependency graph as the domain wants to see it.

    Whole-graph rather than a neighbourhood walk. Cycle detection needs reachability, and at Phase 1
    volumes one indexed scan beats a recursive query issued per candidate edge. When an organization
    has enough dependencies for this to matter, the replacement is a recursive CTE here — the domain
    function does not change, because it takes edges and knows nothing about where they came from.
    """
    rows = session.execute(
        select(Dependency)
        .where(Dependency.org_id == org_id)
        .order_by(Dependency.id)
        .execution_options(populate_existing=True)
    ).scalars()
    return [
        DependencyEdge(
            blocker=Endpoint(type=row.blocker_type, id=row.blocker_id),
            blocked=Endpoint(type=row.blocked_type, id=row.blocked_id),
            kind=DependencyKind(row.kind),
            status=DependencyStatus(row.status),
        )
        for row in rows
    ]


def insert_dependency(session: Session, *, org_id: uuid.UUID, **values: Any) -> Dependency:
    dependency = Dependency(id=uuid7(), org_id=org_id, **values)
    session.add(dependency)
    session.flush()
    return dependency


def update_dependency_status(
    session: Session,
    *,
    org_id: uuid.UUID,
    dependency_id: uuid.UUID,
    expected_version: int,
    status: DependencyStatus,
) -> Dependency:
    _apply(
        session,
        update(Dependency)
        .where(
            Dependency.id == dependency_id,
            Dependency.org_id == org_id,
            Dependency.version == expected_version,
        )
        .values(
            status=status.value, version=Dependency.version + 1, updated_at=func.now()
        ),
        resource="dependency",
        entity_id=dependency_id,
        expected=expected_version,
    )
    dependency = get_dependency(session, org_id=org_id, dependency_id=dependency_id)
    assert dependency is not None  # noqa: S101
    return dependency


def active_dependencies_from(
    session: Session, *, org_id: uuid.UUID, blocker: Endpoint
) -> list[Dependency]:
    """Active edges where this endpoint is the blocker. Drives BR-D-03 and BR-D-05."""
    return list(
        session.execute(
            select(Dependency)
            .where(
                Dependency.org_id == org_id,
                Dependency.blocker_type == blocker.type,
                Dependency.blocker_id == blocker.id,
                Dependency.status == DependencyStatus.ACTIVE.value,
            )
            .order_by(Dependency.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def endpoint_exists(session: Session, *, org_id: uuid.UUID, endpoint: Endpoint) -> bool:
    table = "work" if endpoint.type == "work" else "milestone"
    count = session.execute(
        text(f"SELECT count(*) FROM {table} WHERE id = :id AND org_id = :org_id"),  # noqa: S608
        {"id": endpoint.id, "org_id": org_id},
    ).scalar_one()
    return int(count) > 0


def project_of_endpoint(
    session: Session, *, org_id: uuid.UUID, endpoint: Endpoint
) -> uuid.UUID | None:
    """The project an endpoint belongs to, or None for non-project work (ADR-0029)."""
    if endpoint.type == "milestone":
        milestone = get_milestone(session, org_id=org_id, milestone_id=endpoint.id)
        return milestone.project_id if milestone else None
    work = get_work(session, org_id=org_id, work_id=endpoint.id)
    return work.project_id if work else None


def work_ids_in_teams(
    session: Session, *, org_id: uuid.UUID, team_ids: frozenset[uuid.UUID]
) -> frozenset[uuid.UUID]:
    """Work reachable through a team, i.e. work in a project that team owns.

    Non-project work is deliberately absent: it has no team, and inventing one for it would be the
    synthetic-container mistake ADR-0029 forbids, just spelled differently.
    """
    if not team_ids:
        return frozenset()
    rows = session.execute(
        select(Work.id)
        .join(Project, Project.id == Work.project_id)
        .where(
            Work.org_id == org_id,
            Project.org_id == org_id,
            or_(*[Project.owning_team_id == team_id for team_id in team_ids]),
        )
    ).scalars()
    return frozenset(rows)
