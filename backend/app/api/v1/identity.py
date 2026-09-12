"""`/api/v1` reads over the organization directory.

The organization directory and the writes that maintain it.

Reads are organization-wide: `PERSON`, `TEAM` and `DEPARTMENT` give every role an ORG grant, which
is what a directory is — the people you work with are not a secret from the people you work with.
Writes are not. Creating a Person is `org_admin` only (ADR-0036), a department lead edits inside
their own subtree, a team lead manages their own team's membership, and `ExternalIdentity` is
written by `org_admin` alone with no SELF grant anywhere (ADR-0037).

There is no endpoint that creates an Organization. Provisioning a tenant happens from outside every
tenant, which is why the matrix has no `ORGANIZATION.CREATE` cell and why `ops/dev/seed.py` runs as
the superuser.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    CurrentPrincipal,
    DepartmentCreate,
    DepartmentList,
    DepartmentResource,
    DepartmentUpdate,
    ExternalIdentityCreate,
    ExternalIdentityList,
    ExternalIdentityResource,
    ExternalIdentityUpdate,
    MembershipCreate,
    MembershipResource,
    MembershipStatusChange,
    OrganizationResource,
    OrganizationUpdate,
    PersonCreate,
    PersonList,
    PersonResource,
    PersonStatusChange,
    PersonUpdate,
    RoleAssignmentList,
    RoleAssignmentResource,
    RoleGrant,
    TeamCreate,
    TeamList,
    TeamMemberAdd,
    TeamMembershipList,
    TeamMembershipResource,
    TeamResource,
    TeamUpdate,
    UnitStatusChange,
)
from app.contexts.identity.public import (
    AddTeamMember,
    ChangeMembershipStatus,
    ChangePersonStatus,
    ChangeUnitStatus,
    ConfirmExternalIdentity,
    CreateDepartment,
    CreateExternalIdentity,
    CreateMembership,
    CreatePerson,
    CreateTeam,
    DepartmentService,
    EndTeamMembership,
    ExternalIdentityService,
    GrantRole,
    MembershipService,
    OrganizationService,
    PersonService,
    RevokeRole,
    RoleService,
    ServiceContext,
    TeamService,
    UpdateDepartment,
    UpdateExternalIdentity,
    UpdateOrganization,
    UpdatePerson,
    UpdateTeam,
    external_identity_rows,
    list_departments,
    list_people,
    list_teams,
    role_rows,
    team_member_rows,
)
from app.contexts.identity.public import get_department as read_department
from app.contexts.identity.public import get_person as read_person
from app.contexts.identity.public import get_team as read_team
from app.platform.errors import EntityNotFound
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    PrincipalDep,
    SessionDep,
)
from app.platform.http.etag import etag_for, require_if_match
from app.platform.http.idempotency import Idempotency, replay_response
from app.platform.http.pagination import clamp_limit, decode_cursor
from app.platform.http.validation import CleanText

router = APIRouter(prefix="/api/v1", tags=["identity"], responses=PROBLEM_RESPONSES)

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _context(session: SessionDep, principal: PrincipalDep, actor: ActorDep) -> ServiceContext:
    return ServiceContext(session=session, principal=principal, actor=actor)


def _guard(
    session: SessionDep, principal: PrincipalDep, endpoint: str, key: str | None, payload: object
) -> Idempotency:
    return Idempotency(
        session, org_id=principal.org_id, endpoint=endpoint, key=key, payload=payload
    )


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


# --------------------------------------------------------------------------- organization


@router.patch("/organization", response_model=OrganizationResource)
def update_organization(
    body: OrganizationUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """The caller's own organization. There is no path to any other, and none to create one."""
    expected = require_if_match(if_match)
    updated = OrganizationService(_context(session, principal, actor)).update(
        UpdateOrganization(expected_version=expected, **body.model_dump(exclude_unset=True))
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


# --------------------------------------------------------------------------- person


@router.post("/people", status_code=status.HTTP_201_CREATED, response_model=PersonResource)
def create_person(
    body: PersonCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """ADR-0036. Onboarding is a deliberate act with an audit entry naming who performed it."""
    guard = _guard(
        session, principal, "POST /api/v1/people", idempotency_key, body.model_dump(mode="json")
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    person = PersonService(_context(session, principal, actor)).create(
        CreatePerson(**body.model_dump())
    )
    guard.record(201, PersonResource.model_validate(person).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/people/{person.id}"
    response.headers["ETag"] = etag_for(person.version)
    return person


@router.patch("/people/{person_id}", response_model=PersonResource)
def update_person(
    person_id: uuid.UUID,
    body: PersonUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = PersonService(_context(session, principal, actor)).update(
        UpdatePerson(
            person_id=person_id,
            expected_version=expected,
            **body.model_dump(exclude_unset=True),
        )
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


@router.post("/people/{person_id}/status", response_model=PersonResource)
def change_person_status(
    person_id: uuid.UUID,
    body: PersonStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """BR-I-05. Departing stops new work arriving; it ends nothing that already exists."""
    expected = require_if_match(if_match)
    updated = PersonService(_context(session, principal, actor)).change_status(
        ChangePersonStatus(person_id=person_id, expected_version=expected, target=body.target)
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


# --------------------------------------------------------------------------- membership


@router.post(
    "/memberships", status_code=status.HTTP_201_CREATED, response_model=MembershipResource
)
def create_membership(
    body: MembershipCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """What `resolve_principal` requires to exist before anybody can make a request at all."""
    guard = _guard(
        session,
        principal,
        "POST /api/v1/memberships",
        idempotency_key,
        body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    membership = MembershipService(_context(session, principal, actor)).create(
        CreateMembership(person_id=body.person_id)
    )
    guard.record(201, MembershipResource.model_validate(membership).model_dump(mode="json"))
    response.headers["ETag"] = etag_for(membership.version)
    return membership


@router.post("/memberships/{membership_id}/status", response_model=MembershipResource)
def change_membership_status(
    membership_id: uuid.UUID,
    body: MembershipStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = MembershipService(_context(session, principal, actor)).change_status(
        ChangeMembershipStatus(
            membership_id=membership_id, expected_version=expected, target=body.target
        )
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


# --------------------------------------------------------------------------- department


@router.post(
    "/departments", status_code=status.HTTP_201_CREATED, response_model=DepartmentResource
)
def create_department(
    body: DepartmentCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = _guard(
        session,
        principal,
        "POST /api/v1/departments",
        idempotency_key,
        body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    department = DepartmentService(_context(session, principal, actor)).create(
        CreateDepartment(**body.model_dump())
    )
    guard.record(201, DepartmentResource.model_validate(department).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/departments/{department.id}"
    response.headers["ETag"] = etag_for(department.version)
    return department


@router.patch("/departments/{department_id}", response_model=DepartmentResource)
def update_department(
    department_id: uuid.UUID,
    body: DepartmentUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = DepartmentService(_context(session, principal, actor)).update(
        UpdateDepartment(
            department_id=department_id,
            expected_version=expected,
            **body.model_dump(exclude_unset=True),
        )
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


@router.post("/departments/{department_id}/status", response_model=DepartmentResource)
def change_department_status(
    department_id: uuid.UUID,
    body: UnitStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = DepartmentService(_context(session, principal, actor)).change_status(
        ChangeUnitStatus(entity_id=department_id, expected_version=expected, target=body.target)
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


# --------------------------------------------------------------------------- team


@router.post("/teams", status_code=status.HTTP_201_CREATED, response_model=TeamResource)
def create_team(
    body: TeamCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = _guard(
        session, principal, "POST /api/v1/teams", idempotency_key, body.model_dump(mode="json")
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    team = TeamService(_context(session, principal, actor)).create(CreateTeam(**body.model_dump()))
    guard.record(201, TeamResource.model_validate(team).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/teams/{team.id}"
    response.headers["ETag"] = etag_for(team.version)
    return team


@router.patch("/teams/{team_id}", response_model=TeamResource)
def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = TeamService(_context(session, principal, actor)).update(
        UpdateTeam(
            team_id=team_id, expected_version=expected, **body.model_dump(exclude_unset=True)
        )
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


@router.post("/teams/{team_id}/status", response_model=TeamResource)
def change_team_status(
    team_id: uuid.UUID,
    body: UnitStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = TeamService(_context(session, principal, actor)).change_status(
        ChangeUnitStatus(entity_id=team_id, expected_version=expected, target=body.target)
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


@router.get("/teams/{team_id}/members", response_model=TeamMembershipList)
def list_team_members(
    team_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> TeamMembershipList:
    """Every membership, ended ones included: BR-I-03 keeps them so history stays queryable."""
    if read_team(session, principal, team_id) is None:
        raise EntityNotFound("team", team_id)
    return TeamMembershipList(
        items=[
            TeamMembershipResource.model_validate(row)
            for row in team_member_rows(session, org_id=principal.org_id, team_id=team_id)
        ]
    )


@router.post(
    "/teams/{team_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=TeamMembershipResource,
)
def add_team_member(
    team_id: uuid.UUID,
    body: TeamMemberAdd,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = _guard(
        session,
        principal,
        f"POST /api/v1/teams/{team_id}/members",
        idempotency_key,
        body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    membership = TeamService(_context(session, principal, actor)).add_member(
        AddTeamMember(team_id=team_id, person_id=body.person_id, role=body.role)
    )
    guard.record(201, TeamMembershipResource.model_validate(membership).model_dump(mode="json"))
    response.headers["ETag"] = etag_for(membership.version)
    return membership


@router.delete("/teams/{team_id}/members/{membership_id}", response_model=TeamMembershipResource)
def end_team_member(
    team_id: uuid.UUID,
    membership_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """DELETE on the wire, an update in the database. BR-I-03: the row is never removed."""
    expected = require_if_match(if_match)
    ended = TeamService(_context(session, principal, actor)).end_membership(
        EndTeamMembership(membership_id=membership_id, expected_version=expected)
    )
    if ended.team_id != team_id:
        raise EntityNotFound("team_membership", membership_id)
    response.headers["ETag"] = etag_for(ended.version)
    return ended


# --------------------------------------------------------------------------- roles


@router.get("/people/{person_id}/roles", response_model=RoleAssignmentList)
def list_person_roles(
    person_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> RoleAssignmentList:
    if read_person(session, principal, person_id) is None:
        raise EntityNotFound("person", person_id)
    return RoleAssignmentList(
        items=[
            RoleAssignmentResource.model_validate(row)
            for row in role_rows(session, org_id=principal.org_id, person_id=person_id)
        ]
    )


@router.post(
    "/people/{person_id}/roles",
    status_code=status.HTTP_201_CREATED,
    response_model=RoleAssignmentResource,
)
def grant_role(
    person_id: uuid.UUID,
    body: RoleGrant,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """`org_admin` only. There is no path by which a role grants itself a wider one."""
    guard = _guard(
        session,
        principal,
        f"POST /api/v1/people/{person_id}/roles",
        idempotency_key,
        body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    grant = RoleService(_context(session, principal, actor)).grant(
        GrantRole(
            person_id=person_id,
            role=body.role.value,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
        )
    )
    guard.record(201, RoleAssignmentResource.model_validate(grant).model_dump(mode="json"))
    response.headers["ETag"] = etag_for(grant.version)
    return grant


@router.delete("/roles/{assignment_id}", response_model=RoleAssignmentResource)
def revoke_role(
    assignment_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """Revoked, not deleted: who held what and when is the history an auditor comes for."""
    expected = require_if_match(if_match)
    revoked = RoleService(_context(session, principal, actor)).revoke(
        RevokeRole(assignment_id=assignment_id, expected_version=expected)
    )
    response.headers["ETag"] = etag_for(revoked.version)
    return revoked


# --------------------------------------------------------------------------- external identity


@router.get("/people/{person_id}/external-identities", response_model=ExternalIdentityList)
def list_person_external_identities(
    person_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> ExternalIdentityList:
    if read_person(session, principal, person_id) is None:
        raise EntityNotFound("person", person_id)
    return ExternalIdentityList(
        items=[
            ExternalIdentityResource.model_validate(row)
            for row in external_identity_rows(
                session, org_id=principal.org_id, person_id=person_id
            )
        ]
    )


@router.post(
    "/people/{person_id}/external-identities",
    status_code=status.HTTP_201_CREATED,
    response_model=ExternalIdentityResource,
)
def create_external_identity(
    person_id: uuid.UUID,
    body: ExternalIdentityCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """Created unconfirmed. Confirming it is a separate act by a separate permission (ADR-0037)."""
    guard = _guard(
        session,
        principal,
        f"POST /api/v1/people/{person_id}/external-identities",
        idempotency_key,
        body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    mapping = ExternalIdentityService(_context(session, principal, actor)).create(
        CreateExternalIdentity(person_id=person_id, **body.model_dump())
    )
    guard.record(201, ExternalIdentityResource.model_validate(mapping).model_dump(mode="json"))
    response.headers["ETag"] = etag_for(mapping.version)
    return mapping


@router.patch("/external-identities/{identity_id}", response_model=ExternalIdentityResource)
def update_external_identity(
    identity_id: uuid.UUID,
    body: ExternalIdentityUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = ExternalIdentityService(_context(session, principal, actor)).update(
        UpdateExternalIdentity(
            identity_id=identity_id,
            expected_version=expected,
            **body.model_dump(exclude_unset=True),
        )
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


@router.post(
    "/external-identities/{identity_id}/confirm", response_model=ExternalIdentityResource
)
def confirm_external_identity(
    identity_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """BR-I-06. Records who decided this handle belongs to this person, and when."""
    expected = require_if_match(if_match)
    confirmed = ExternalIdentityService(_context(session, principal, actor)).confirm(
        ConfirmExternalIdentity(identity_id=identity_id, expected_version=expected)
    )
    response.headers["ETag"] = etag_for(confirmed.version)
    return confirmed
