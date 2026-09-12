"""Read side of the Work Core.

Reads are authorized by *filtering*, not by fetching and then deciding. A row a principal may not
read is never selected, so there is no moment at which it exists in memory to be leaked by a bug in
a serializer, a log line or an error message. A single item read is the same query with an id
predicate, so `GET /work/{id}` and `GET /work` cannot drift apart: one returns nothing and becomes a
404, the other returns one fewer row.

Two independent conditions have to hold, and the SQL below is their conjunction:

* **Role reach** — the authorization matrix's answer for `(WORK, READ)`, translated from grants into
  predicates. The matrix stays the only place that says which role gets which grant; `grants_for`
  reports its answer and this module renders it.
* **Visibility** — BR-W-18. Narrows, never widens, and is evaluated in addition to reach rather than
  instead of it (security-model §3.2).

The absence of a row must not be observable. That rules out `total` counts, and it rules out
filtering a fetched page — a page of 50 that returns 43 because 7 were removed reports that 7 exist.
The filter is in the `WHERE` clause so the page is full of rows the caller may actually see.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.contexts.work.authorization import ActorReach, reach_of
from app.contexts.work.domain import (
    VISIBILITY_EXEMPT_ROLES,
    ProjectStatus,
    WorkPartition,
    WorkStatus,
)
from app.contexts.work.models import Dependency, Milestone, Project, Work, WorkAssignment
from app.platform.authz import Action, Grant, Principal, ResourceType, grants_for
from app.platform.http.pagination import Cursor, encode_cursor


@dataclass(frozen=True, slots=True)
class WorkFilter:
    """Everything a caller may narrow a list by. Absent means "do not narrow"."""

    project_id: uuid.UUID | None = None
    status: WorkStatus | None = None
    partition: WorkPartition | None = None
    owner_person_id: uuid.UUID | None = None
    due_before: dt.date | None = None


@dataclass(frozen=True, slots=True)
class WorkPage:
    items: list[Work]
    next_cursor: str | None


def _reach_predicate(principal: Principal, reach: ActorReach) -> Any:
    """The matrix's grants for (WORK, READ), rendered as SQL."""
    grants = grants_for(principal, Action.READ, ResourceType.WORK)
    if Grant.ORG in grants:
        return None  # no narrowing by reach; visibility may still narrow

    clauses = []
    if Grant.DEPARTMENT in grants:
        clauses.append(_department_reach(reach))
    if Grant.TEAM in grants:
        clauses.append(Project.owning_team_id.in_(reach.team_ids))
    if Grant.PERSONAL in grants:
        clauses.append(_personal_reach(reach))
    if not clauses:
        # No grant at all. Not an error — `viewer` scoped elsewhere, or a role that simply does not
        # read Work. It must return nothing rather than everything.
        return _never()
    return or_(*clauses)


def _department_reach(reach: ActorReach) -> Any:
    return or_(
        Project.department_id.in_(reach.department_ids),
        Project.owning_team_id.in_(reach.department_team_ids),
    )


def _personal_reach(reach: ActorReach) -> Any:
    """Assigned to it, or wrote it down. The second half is W-1 and the reason 0006 exists."""
    return or_(
        Work.id.in_(reach.assigned_work_ids),
        Work.created_by_person_id == reach.person_id,
    )


def _visibility_predicate(principal: Principal, reach: ActorReach) -> Any:
    """BR-W-18.

    Note what is *not* special-cased: non-project Work. Its `project_id` is null, so the outer join
    below yields nulls, so both the department and the team clauses are false, and it falls to the
    floor — creator, active assignees, `org_admin`, `auditor`. That is BR-W-18's last sentence, and
    it is enforced by the shape of the data rather than by a branch that could be forgotten.
    """
    if principal.roles & VISIBILITY_EXEMPT_ROLES:
        return None

    floor = _personal_reach(reach)
    return or_(
        floor,
        Work.visibility == "organization",
        and_(Work.visibility.in_(("department", "team")), _department_reach(reach)),
        and_(Work.visibility == "team", Project.owning_team_id.in_(reach.team_ids)),
    )


def _never() -> Any:
    return Work.id.is_(None)


def readable_work(principal: Principal, reach: ActorReach) -> Select[tuple[Work]]:
    """Every Work this principal may read, and nothing else. The base of both read paths."""
    statement = (
        select(Work)
        .outerjoin(Project, and_(Project.id == Work.project_id, Project.org_id == Work.org_id))
        .where(Work.org_id == principal.org_id)
    )
    for predicate in (
        _reach_predicate(principal, reach),
        _visibility_predicate(principal, reach),
    ):
        if predicate is not None:
            statement = statement.where(predicate)
    return statement


def get_work(
    session: Session, principal: Principal, work_id: uuid.UUID
) -> Work | None:
    """One Work, or None if it does not exist *or* may not be read. Both become 404.

    Deliberately not two outcomes. "Exists but you may not see it" and "does not exist" are the same
    answer to anyone not entitled to the difference (contract §5).
    """
    reach = reach_of(session, principal)
    return session.execute(
        readable_work(principal, reach)
        .where(Work.id == work_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_work(
    session: Session,
    principal: Principal,
    *,
    filters: WorkFilter | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> WorkPage:
    """A page of readable Work, oldest first by `(created_at, id)`.

    The sort key is the cursor, and it is the whole key: `created_at` alone is not unique, and a tie
    straddling a page boundary would drop or repeat a row. One extra row is fetched to decide
    whether a next page exists, which answers that question without a count.
    """
    reach = reach_of(session, principal)
    statement = readable_work(principal, reach)
    narrowed = filters or WorkFilter()

    if narrowed.project_id is not None:
        statement = statement.where(Work.project_id == narrowed.project_id)
    if narrowed.status is not None:
        statement = statement.where(Work.status == narrowed.status.value)
    if narrowed.partition is WorkPartition.PROJECT:
        statement = statement.where(Work.project_id.is_not(None))
    elif narrowed.partition is WorkPartition.NON_PROJECT:
        # BR-RPT-01. Non-project Work is a partition of the whole, not a leftover, and asking for it
        # is a first-class query rather than a filter nobody set.
        statement = statement.where(Work.project_id.is_(None))
    if narrowed.owner_person_id is not None:
        statement = statement.where(
            Work.id.in_(
                select(WorkAssignment.work_id).where(
                    WorkAssignment.org_id == principal.org_id,
                    WorkAssignment.person_id == narrowed.owner_person_id,
                    WorkAssignment.role == "OWNER",
                    WorkAssignment.status == "active",
                )
            )
        )
    if narrowed.due_before is not None:
        statement = statement.where(Work.due_date.is_not(None), Work.due_date < narrowed.due_before)

    if cursor is not None:
        statement = statement.where(
            or_(
                Work.created_at > cursor.created_at,
                and_(Work.created_at == cursor.created_at, Work.id > cursor.id),
            )
        )

    rows = list(
        session.execute(
            statement.order_by(Work.created_at, Work.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if len(rows) > limit:
        last = rows[limit - 1]
        return WorkPage(items=rows[:limit], next_cursor=encode_cursor(last.created_at, last.id))
    return WorkPage(items=rows, next_cursor=None)


# --------------------------------------------------------------------------- project


def _project_reach_predicate(principal: Principal, reach: ActorReach) -> Any:
    grants = grants_for(principal, Action.READ, ResourceType.PROJECT)
    if Grant.ORG in grants:
        return None

    clauses = []
    if Grant.DEPARTMENT in grants:
        clauses.append(_department_reach(reach))
    if Grant.TEAM in grants:
        clauses.append(Project.owning_team_id.in_(reach.team_ids))
    if Grant.PERSONAL in grants:
        clauses.append(_project_floor(reach))
    if not clauses:
        return Project.id.is_(None)
    return or_(*clauses)


def _project_floor(reach: ActorReach) -> Any:
    """BR-P-09's floor: the people a Project is readable by at every level."""
    return or_(
        Project.lead_person_id == reach.person_id,
        Project.sponsor_person_id == reach.person_id,
        Project.created_by_person_id == reach.person_id,
    )


def _project_visibility_predicate(principal: Principal, reach: ActorReach) -> Any:
    """BR-P-09, the same shape as BR-W-18 one level up."""
    if principal.roles & VISIBILITY_EXEMPT_ROLES:
        return None
    return or_(
        _project_floor(reach),
        Project.visibility == "organization",
        and_(Project.visibility.in_(("department", "team")), _department_reach(reach)),
        and_(Project.visibility == "team", Project.owning_team_id.in_(reach.team_ids)),
    )


def readable_projects(principal: Principal, reach: ActorReach) -> Select[tuple[Project]]:
    statement = select(Project).where(Project.org_id == principal.org_id)
    for predicate in (
        _project_reach_predicate(principal, reach),
        _project_visibility_predicate(principal, reach),
    ):
        if predicate is not None:
            statement = statement.where(predicate)
    return statement


def get_project(
    session: Session, principal: Principal, project_id: uuid.UUID
) -> Project | None:
    reach = reach_of(session, principal)
    return session.execute(
        readable_projects(principal, reach)
        .where(Project.id == project_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class ProjectFilter:
    status: ProjectStatus | None = None
    owning_team_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProjectPage:
    items: list[Project]
    next_cursor: str | None


def list_projects(
    session: Session,
    principal: Principal,
    *,
    filters: ProjectFilter | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> ProjectPage:
    reach = reach_of(session, principal)
    statement = readable_projects(principal, reach)
    narrowed = filters or ProjectFilter()
    if narrowed.status is not None:
        statement = statement.where(Project.status == narrowed.status.value)
    if narrowed.owning_team_id is not None:
        statement = statement.where(Project.owning_team_id == narrowed.owning_team_id)
    if cursor is not None:
        statement = statement.where(
            or_(
                Project.created_at > cursor.created_at,
                and_(Project.created_at == cursor.created_at, Project.id > cursor.id),
            )
        )
    rows = list(
        session.execute(
            statement.order_by(Project.created_at, Project.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if len(rows) > limit:
        last = rows[limit - 1]
        return ProjectPage(rows[:limit], encode_cursor(last.created_at, last.id))
    return ProjectPage(rows, None)


# --------------------------------------------------------------------------- milestone


def readable_milestones(
    principal: Principal, reach: ActorReach
) -> Select[tuple[Milestone]]:
    """A Milestone is readable exactly when its Project is.

    It carries no visibility of its own and cannot be moved between Projects (BR-P-05, BR-P-09), so
    deriving the predicate rather than duplicating it means the two can never disagree.
    """
    return (
        select(Milestone)
        .where(Milestone.org_id == principal.org_id)
        .where(
            Milestone.project_id.in_(
                readable_projects(principal, reach).with_only_columns(Project.id)
            )
        )
    )


def get_milestone(
    session: Session, principal: Principal, milestone_id: uuid.UUID
) -> Milestone | None:
    reach = reach_of(session, principal)
    return session.execute(
        readable_milestones(principal, reach)
        .where(Milestone.id == milestone_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_milestones(
    session: Session, principal: Principal, *, project_id: uuid.UUID
) -> list[Milestone]:
    """Every Milestone of one Project, ordered as the Project orders them.

    Not paginated. A Project with more milestones than a page holds is a Project that has stopped
    using milestones for what they are, and inventing a cursor for a list that is always small would
    be paying for a problem nobody has (contract §17).
    """
    reach = reach_of(session, principal)
    return list(
        session.execute(
            readable_milestones(principal, reach)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.order_index, Milestone.created_at, Milestone.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


# --------------------------------------------------------------------------- dependency


def get_dependency(
    session: Session, principal: Principal, dependency_id: uuid.UUID
) -> Dependency | None:
    """Readable when either endpoint is.

    Matches how a dependency is authorized for writing (Checkpoint 3): it is a statement about two
    things, and the person who needs to see it is usually related to one of them. The serialised
    form carries endpoint ids and types only — never the other item's title — so learning that your
    work is blocked by `work:5f3a` does not reveal what `work:5f3a` says.
    """
    reach = reach_of(session, principal)
    work_ids = readable_work(principal, reach).with_only_columns(Work.id)
    milestone_ids = readable_milestones(principal, reach).with_only_columns(Milestone.id)
    return session.execute(
        select(Dependency)
        .where(Dependency.org_id == principal.org_id, Dependency.id == dependency_id)
        .where(
            or_(
                and_(Dependency.blocker_type == "work", Dependency.blocker_id.in_(work_ids)),
                and_(Dependency.blocked_type == "work", Dependency.blocked_id.in_(work_ids)),
                and_(
                    Dependency.blocker_type == "milestone",
                    Dependency.blocker_id.in_(milestone_ids),
                ),
                and_(
                    Dependency.blocked_type == "milestone",
                    Dependency.blocked_id.in_(milestone_ids),
                ),
            )
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_dependencies_of_work(
    session: Session, principal: Principal, *, work_id: uuid.UUID
) -> list[Dependency]:
    """Both directions: what blocks this Work, and what this Work blocks.

    One list rather than two endpoints, because "what is the state of this item" is one question.
    The caller distinguishes the directions from the endpoints already in each row.
    """
    return list(
        session.execute(
            select(Dependency)
            .where(
                Dependency.org_id == principal.org_id,
                or_(
                    and_(
                        Dependency.blocker_type == "work", Dependency.blocker_id == work_id
                    ),
                    and_(
                        Dependency.blocked_type == "work", Dependency.blocked_id == work_id
                    ),
                ),
            )
            .order_by(Dependency.created_at, Dependency.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


# --------------------------------------------------------------------------- assignment


def list_assignments(
    session: Session, principal: Principal, *, work_id: uuid.UUID
) -> list[WorkAssignment]:
    """Every assignment on one Work, ended ones included.

    History is the point: BR-W-14 keeps ended rows precisely so that "who owned this in March" is
    answerable, and a list that hid them would answer only "who owns it now", which the resource
    itself already implies.
    """
    return list(
        session.execute(
            select(WorkAssignment)
            .where(
                WorkAssignment.org_id == principal.org_id,
                WorkAssignment.work_id == work_id,
            )
            .order_by(WorkAssignment.assigned_at, WorkAssignment.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )
