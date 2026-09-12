"""`/api/v1` reads over the organization directory.

Read-only, and that is the scope rather than an omission. Every Work Core write already names a
Person, a Team or a Department — an assignment needs somebody to assign to, and BR-P-01 requires a
Project to have an owning Team or Department — so a client cannot complete a single journey without
being able to look those up. Creating and editing them is a separate body of rules (BR-I-01 to
BR-I-07) and belongs with the checkpoint that implements them.

No new authorization cells. `PERSON`, `TEAM` and `DEPARTMENT` already have `READ` and `LIST` rows in
the matrix and every role holds an ORG grant on them, which is what a directory is: the people you
work with are not a secret from the people you work with. The filtering still runs through
`grants_for`, so a role added later with a narrower grant narrows this too.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    CurrentPrincipal,
    DepartmentList,
    DepartmentResource,
    PersonList,
    PersonResource,
    TeamList,
    TeamResource,
)
from app.contexts.identity.public import (
    get_department as read_department,
)
from app.contexts.identity.public import (
    get_person as read_person,
)
from app.contexts.identity.public import (
    get_team as read_team,
)
from app.contexts.identity.public import (
    list_departments,
    list_people,
    list_teams,
)
from app.platform.errors import EntityNotFound
from app.platform.http.deps import PrincipalDep, SessionDep
from app.platform.http.etag import etag_for
from app.platform.http.pagination import clamp_limit, decode_cursor
from app.platform.http.validation import CleanText

router = APIRouter(prefix="/api/v1", tags=["identity"], responses=PROBLEM_RESPONSES)


@router.get("/me", response_model=CurrentPrincipal)
def read_me(session: SessionDep, principal: PrincipalDep) -> Any:
    """Who the caller is, in this organization.

    The token says which human is calling; it deliberately never says which organization or what
    they may do there (security-model §2). This is where those two questions are answered, and it is
    what lets a client render navigation that matches the caller's roles instead of rendering
    everything and discovering the refusals one request at a time.
    """
    person = read_person(session, principal, principal.person_id)
    if person is None:  # pragma: no cover - resolve_principal already proved it exists
        raise EntityNotFound("person", principal.person_id)
    return CurrentPrincipal(
        person_id=person.id,
        org_id=principal.org_id,
        display_name=person.display_name,
        email=person.email,
        roles=sorted(role.value for role in principal.roles),
    )


@router.get("/people", response_model=PersonList)
def list_organization_people(
    session: SessionDep,
    principal: PrincipalDep,
    q: Annotated[CleanText | None, Query(max_length=200)] = None,
    include_departed: Annotated[bool, Query()] = False,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> PersonList:
    people, next_cursor = list_people(
        session,
        principal,
        query=q,
        include_departed=include_departed,
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return PersonList(
        items=[PersonResource.model_validate(person) for person in people],
        next_cursor=next_cursor,
    )


@router.get("/people/{person_id}", response_model=PersonResource)
def read_person_by_id(
    person_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> Any:
    person = read_person(session, principal, person_id)
    if person is None:
        raise EntityNotFound("person", person_id)
    response.headers["ETag"] = etag_for(person.version)
    return person


@router.get("/teams", response_model=TeamList)
def list_organization_teams(
    session: SessionDep,
    principal: PrincipalDep,
    department_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> TeamList:
    teams, next_cursor = list_teams(
        session,
        principal,
        department_id=department_id,
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return TeamList(
        items=[TeamResource.model_validate(team) for team in teams],
        next_cursor=next_cursor,
    )


@router.get("/teams/{team_id}", response_model=TeamResource)
def read_team_by_id(
    team_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> Any:
    team = read_team(session, principal, team_id)
    if team is None:
        raise EntityNotFound("team", team_id)
    response.headers["ETag"] = etag_for(team.version)
    return team


@router.get("/departments", response_model=DepartmentList)
def list_organization_departments(
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> DepartmentList:
    departments, next_cursor = list_departments(
        session,
        principal,
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return DepartmentList(
        items=[DepartmentResource.model_validate(d) for d in departments],
        next_cursor=next_cursor,
    )


@router.get("/departments/{department_id}", response_model=DepartmentResource)
def read_department_by_id(
    department_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> Any:
    department = read_department(session, principal, department_id)
    if department is None:
        raise EntityNotFound("department", department_id)
    response.headers["ETag"] = etag_for(department.version)
    return department
