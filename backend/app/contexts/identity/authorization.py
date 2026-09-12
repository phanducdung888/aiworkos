"""Relationship facts for Identity resources.

`platform/authz` decides; this supplies what it decides on. The Work Core has its own version of
this file for its own resources, and the two deliberately do not share code: what "in my department"
means for a Work item and for a Person are different questions with different answers, and a shared
helper would be a place for one of them to quietly acquire the other's meaning.

Identity depends on nothing (ADR-0035). Everything below is answered from Identity's own rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.contexts.identity.queries import (
    departments_led_by,
    team_ids_for_person,
    team_ids_in_departments,
)
from app.platform.authz import Principal, Relation


@dataclass(frozen=True, slots=True)
class ActorReach:
    """The principal's position in the organization, resolved once per request."""

    person_id: uuid.UUID
    org_id: uuid.UUID
    team_ids: frozenset[uuid.UUID]
    department_ids: frozenset[uuid.UUID]
    department_team_ids: frozenset[uuid.UUID]


def reach_of(session: Session, principal: Principal) -> ActorReach:
    departments = departments_led_by(
        session, org_id=principal.org_id, person_id=principal.person_id
    )
    return ActorReach(
        person_id=principal.person_id,
        org_id=principal.org_id,
        team_ids=team_ids_for_person(
            session, org_id=principal.org_id, person_id=principal.person_id
        ),
        department_ids=departments,
        department_team_ids=team_ids_in_departments(
            session, org_id=principal.org_id, department_ids=departments
        ),
    )


def department_relations(reach: ActorReach, department_id: uuid.UUID) -> frozenset[Relation]:
    """A department lead reaches their own subtree, which `departments_led_by` already expands."""
    return (
        frozenset({Relation.IN_DEPARTMENT})
        if department_id in reach.department_ids
        else frozenset()
    )


def team_relations(
    reach: ActorReach, *, team_id: uuid.UUID, department_id: uuid.UUID | None
) -> frozenset[Relation]:
    relations: set[Relation] = set()
    if team_id in reach.team_ids:
        relations.add(Relation.IN_TEAM)
    if department_id is not None and department_id in reach.department_ids:
        relations.add(Relation.IN_DEPARTMENT)
    if team_id in reach.department_team_ids:
        relations.add(Relation.IN_DEPARTMENT)
    return frozenset(relations)


def person_relations(
    session: Session, reach: ActorReach, person_id: uuid.UUID
) -> frozenset[Relation]:
    """A Person belongs to no department directly; they reach one through their teams.

    So "is this person in my department" is "is any team they are in inside a department I lead".
    Computed rather than assumed, because the alternative — treating every Person in the
    organization as within a department lead's reach — would make the DEPT grant on `PERSON.UPDATE`
    indistinguishable from an ORG one.
    """
    relations: set[Relation] = set()
    if person_id == reach.person_id:
        relations.add(Relation.SELF)
    if reach.department_team_ids:
        their_teams = team_ids_for_person(session, org_id=reach.org_id, person_id=person_id)
        if their_teams & reach.department_team_ids:
            relations.add(Relation.IN_DEPARTMENT)
        if their_teams & reach.team_ids:
            relations.add(Relation.IN_TEAM)
    return frozenset(relations)


def own_record_relations(reach: ActorReach, person_id: uuid.UUID) -> frozenset[Relation]:
    """For resources whose only narrow grant is SELF — a person's own role assignments."""
    return frozenset({Relation.SELF}) if person_id == reach.person_id else frozenset()
