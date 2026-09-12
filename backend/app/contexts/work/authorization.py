"""Relationship facts for Work Core resources.

`platform/authz` decides. This module supplies the facts it decides on, because only the Work Core
knows what "in my team" means for a Work item — and the policy engine is deliberately forbidden from
asking the database itself.

The interesting case is non-project Work. It has no project, therefore no owning team, therefore no
team or department relationship to inherit; its only relationship to a human is assignment
(ADR-0029, BR-W-07). That is not a gap to be patched by inventing a container for it (BR-P-07a): it
is the shape of the thing, and the matrix already accounts for it by giving `member` an ORG grant on
`WORK.CREATE`.

`reach_of` is one query set per request, not per resource. Authorizing a list of fifty work items
must not issue a hundred membership queries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

import app.contexts.identity.public as identity
from app.contexts.work import repository
from app.contexts.work.models import Dependency, Milestone, Project, Work, WorkAssignment
from app.platform.authz import Principal, Relation, ResourceRef, ResourceType


@dataclass(frozen=True, slots=True)
class ActorReach:
    """Everything about the principal's position in the organization, resolved once."""

    person_id: uuid.UUID
    org_id: uuid.UUID
    team_ids: frozenset[uuid.UUID]
    department_ids: frozenset[uuid.UUID]
    #: Teams sitting inside a department this person leads. A department lead reaches a project
    #: through its owning team as well as through its department, and only one of the two is set
    #: on any given project.
    department_team_ids: frozenset[uuid.UUID]
    assigned_work_ids: frozenset[uuid.UUID]


def reach_of(session: Session, principal: Principal) -> ActorReach:
    departments = identity.departments_led_by(
        session, org_id=principal.org_id, person_id=principal.person_id
    )
    return ActorReach(
        person_id=principal.person_id,
        org_id=principal.org_id,
        team_ids=identity.team_ids_for_person(
            session, org_id=principal.org_id, person_id=principal.person_id
        ),
        department_ids=departments,
        department_team_ids=identity.team_ids_in_departments(
            session, org_id=principal.org_id, department_ids=departments
        ),
        assigned_work_ids=repository.work_ids_assigned_to(
            session, org_id=principal.org_id, person_id=principal.person_id
        ),
    )


def project_relations(reach: ActorReach, project: Project) -> frozenset[Relation]:
    relations: set[Relation] = set()
    if project.owning_team_id is not None and project.owning_team_id in reach.team_ids:
        relations.add(Relation.IN_TEAM)
    in_led_department = (
        project.department_id is not None and project.department_id in reach.department_ids
    ) or (
        project.owning_team_id is not None
        and project.owning_team_id in reach.department_team_ids
    )
    if in_led_department:
        relations.add(Relation.IN_DEPARTMENT)
    if reach.person_id in {project.lead_person_id, project.sponsor_person_id}:
        relations.add(Relation.PERSONAL)
    return frozenset(relations)


def milestone_relations(reach: ActorReach, project: Project | None) -> frozenset[Relation]:
    """A milestone belongs to exactly one project and cannot move (BR-P-05), so it inherits."""
    return project_relations(reach, project) if project is not None else frozenset()


def work_relations(
    reach: ActorReach, work: Work, project: Project | None
) -> frozenset[Relation]:
    """Being on the work is the personal relation. Having written it down is not — see below."""
    relations = set(milestone_relations(reach, project))
    if work.id in reach.assigned_work_ids:
        relations.add(Relation.PERSONAL)
    return frozenset(relations)


def work_authorship_relations(
    reach: ActorReach, work: Work, project: Project | None
) -> frozenset[Relation]:
    """`work_relations`, plus the permanent relation the person who captured the Work keeps.

    Reaches `WORK.READ`, `WORK.LIST`, `WORK.UPDATE` and `WORK.CHANGE_STATE`. The PO decision at
    Checkpoint 3.5 named UPDATE and CHANGE_STATE; READ and LIST are included because UPDATE without
    READ is not a weaker permission, it is an incoherent one — the caller would PATCH a resource
    that 404s on GET, and Checkpoint 4a's own D1 (a creator claiming OWNER of their own Work) cannot
    be performed over HTTP without first reading it. Recorded as an interpretation in the Checkpoint
    4a report rather than assumed silently.

    Authorship is what makes non-project Work workable at all: it has no project, therefore no team,
    therefore nothing else to derive a relation from, so without this the member who captured "check
    the IOC API" could neither find it, correct it nor finish it (ADR-0029, BR-W-07, and the same
    "capturing user" MON-001 and MON-002a escalate to).

    It does not extend to `CHANGE_VISIBILITY`, `REASSIGN` or `END_ASSIGNMENT`, and it reaches
    `ASSIGN` only under the paired condition in `assignment_creation_relations`. Writing something
    down is a claim on the description of the work, not on who is allowed to see it or who else is
    allowed to do it.
    """
    relations = set(work_relations(reach, work, project))
    if work.created_by_person_id is not None and work.created_by_person_id == reach.person_id:
        relations.add(Relation.PERSONAL)
    return frozenset(relations)


def assignment_creation_relations(
    reach: ActorReach,
    work: Work,
    project: Project | None,
    *,
    person_id: uuid.UUID | None = None,
) -> frozenset[Relation]:
    """Relations for putting somebody *onto* work.

    Being on the work yourself is enough (ASSIGN = OWN in the matrix). Authorship is enough too, but
    only to claim the work for yourself — `person_id` is the person being assigned, and the creator
    condition holds only when that person is the creator (PO decision D1, Checkpoint 4a).

    The pairing is the whole rule and the two halves do not separate. Taking work you wrote down is
    the capture flow the product exists for: paste a conversation, get a Work item with no assignee,
    claim it. Handing that same work to somebody else is staffing, and a member who captured a note
    has no more claim to direct another person's time than any other member does — which is why
    `ASSIGN` and `REASSIGN` were split apart in the first place (ADR-0032).
    """
    relations = set(work_relations(reach, work, project))
    claims_it_for_themselves = (
        person_id is not None
        and person_id == reach.person_id
        and work.created_by_person_id == reach.person_id
    )
    if claims_it_for_themselves:
        relations.add(Relation.PERSONAL)
    return frozenset(relations)


def assignment_relations(
    reach: ActorReach,
    work: Work,
    project: Project | None,
    assignment: WorkAssignment,
) -> frozenset[Relation]:
    """Relations to one existing assignment. PERSONAL here means *this assignment is mine*.

    Narrower than the work's own relations, and that narrowness is the point. Inheriting the work's
    PERSONAL would mean any member on a work item could end any *other* member's assignment — and
    then assign themselves, which is `REASSIGN` reached by two calls that individually look like
    `ASSIGN` and `END_ASSIGNMENT`. The matrix denies `member` REASSIGN precisely so that ownership
    cannot change hands on a member's say-so (ADR-0032); a relation that hands back the same power
    through the side door would make that denial decorative.

    The structural relations still come from the work, so a team lead or department lead reaches
    the assignments in their reach exactly as they reach the work itself.
    """
    relations = set(milestone_relations(reach, project))
    if assignment.person_id == reach.person_id:
        relations.add(Relation.PERSONAL)
    return frozenset(relations)


def dependency_relations(
    session: Session, reach: ActorReach, *, endpoints: tuple[tuple[str, uuid.UUID], ...]
) -> frozenset[Relation]:
    """The union of the actor's relations to both endpoints.

    Union rather than intersection on purpose. A dependency is a statement that one side constrains
    the other, and the person best placed to record it is usually related to exactly one of them.
    Requiring a relationship to both would make the common case — "my work is waiting on another
    team's milestone" — unrecordable by the only person who knows about it.
    """
    relations: set[Relation] = set()
    for endpoint_type, endpoint_id in endpoints:
        if endpoint_type == "work":
            work = repository.get_work(session, org_id=reach.org_id, work_id=endpoint_id)
            if work is None:
                continue
            project = _project_of(session, reach.org_id, work.project_id)
            relations |= work_relations(reach, work, project)
        else:
            milestone = repository.get_milestone(
                session, org_id=reach.org_id, milestone_id=endpoint_id
            )
            if milestone is None:
                continue
            project = _project_of(session, reach.org_id, milestone.project_id)
            relations |= milestone_relations(reach, project)
    return frozenset(relations)


def dependency_endpoints(dependency: Dependency) -> tuple[tuple[str, uuid.UUID], ...]:
    return (
        (dependency.blocker_type, dependency.blocker_id),
        (dependency.blocked_type, dependency.blocked_id),
    )


def _project_of(
    session: Session, org_id: uuid.UUID, project_id: uuid.UUID | None
) -> Project | None:
    if project_id is None:
        return None
    return repository.get_project(session, org_id=org_id, project_id=project_id)


def ref(
    resource_type: ResourceType,
    org_id: uuid.UUID,
    relations: frozenset[Relation],
    resource_id: uuid.UUID | None = None,
) -> ResourceRef:
    return ResourceRef(
        type=resource_type, org_id=org_id, id=resource_id, relations=relations
    )


def milestone_project(session: Session, milestone: Milestone) -> Project | None:
    return _project_of(session, milestone.org_id, milestone.project_id)


def work_project(session: Session, work: Work) -> Project | None:
    return _project_of(session, work.org_id, work.project_id)
