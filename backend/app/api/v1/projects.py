"""`/api/v1/projects` and the Milestones nested under them.

Milestones are nested because a Milestone cannot exist apart from its Project and cannot be moved
between Projects (BR-P-05). A top-level `/milestones` collection would suggest otherwise, and a URL
that implies a relationship the domain forbids is a URL somebody will eventually try to use.

Reads by id stay flat (`/api/v1/milestones/{id}`): once you hold the id, the Project is implied and
repeating it in the path would only create a second way to be wrong.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    MilestoneCreate,
    MilestoneList,
    MilestoneResource,
    MilestoneStatusChange,
    MilestoneUpdate,
    ProjectCreate,
    ProjectList,
    ProjectResource,
    ProjectStatusChange,
    ProjectUpdate,
)
from app.contexts.work.public import (
    ChangeMilestoneStatus,
    ChangeProjectStatus,
    ChangeProjectVisibility,
    CreateMilestone,
    CreateProject,
    EntityNotFound,
    MilestoneService,
    ProjectFilter,
    ProjectService,
    ProjectStatus,
    ServiceContext,
    UpdateMilestone,
    UpdateProject,
)
from app.contexts.work.public import get_milestone as get_readable_milestone
from app.contexts.work.public import get_project as get_readable_project
from app.contexts.work.public import list_milestones as list_readable_milestones
from app.contexts.work.public import list_projects as list_readable_projects
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    PrincipalDep,
    SessionDep,
)
from app.platform.http.etag import etag_for, require_if_match
from app.platform.http.idempotency import Idempotency, replay_response
from app.platform.http.pagination import clamp_limit, decode_cursor

projects = APIRouter(prefix="/api/v1/projects", tags=["projects"], responses=PROBLEM_RESPONSES)
milestones = APIRouter(
    prefix="/api/v1/milestones", tags=["milestones"], responses=PROBLEM_RESPONSES
)

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _context(session: SessionDep, principal: PrincipalDep, actor: ActorDep) -> ServiceContext:
    return ServiceContext(session=session, principal=principal, actor=actor)


def _tagged(response: Response, version: int) -> None:
    response.headers["ETag"] = etag_for(version)


def _load_project(session: SessionDep, principal: PrincipalDep, project_id: uuid.UUID) -> Any:
    project = get_readable_project(session, principal, project_id)
    if project is None:
        raise EntityNotFound("project", project_id)
    return project


def _load_milestone(
    session: SessionDep, principal: PrincipalDep, milestone_id: uuid.UUID
) -> Any:
    milestone = get_readable_milestone(session, principal, milestone_id)
    if milestone is None:
        raise EntityNotFound("milestone", milestone_id)
    return milestone


# --------------------------------------------------------------------------- project


@projects.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectResource)
def create_project(
    body: ProjectCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint="POST /api/v1/projects",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    project = ProjectService(_context(session, principal, actor)).create(
        CreateProject(**body.model_dump())
    )
    guard.record(201, ProjectResource.model_validate(project).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/projects/{project.id}"
    _tagged(response, project.version)
    return project


@projects.get("", response_model=ProjectList)
def list_projects(
    session: SessionDep,
    principal: PrincipalDep,
    project_status: Annotated[ProjectStatus | None, Query(alias="status")] = None,
    owning_team_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> ProjectList:
    page = list_readable_projects(
        session,
        principal,
        filters=ProjectFilter(status=project_status, owning_team_id=owning_team_id),
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return ProjectList(
        items=[ProjectResource.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@projects.get("/{project_id}", response_model=ProjectResource)
def read_project(
    project_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> Any:
    project = _load_project(session, principal, project_id)
    _tagged(response, project.version)
    return project


@projects.patch("/{project_id}", response_model=ProjectResource)
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    _load_project(session, principal, project_id)

    changes = body.model_dump(exclude_unset=True)
    visibility = changes.pop("visibility", None)
    updated = ProjectService(_context(session, principal, actor)).update(
        UpdateProject(project_id=project_id, expected_version=expected, **changes)
    )
    if visibility is not None:  # pragma: no cover - not part of ProjectUpdate today
        updated = ProjectService(_context(session, principal, actor)).change_visibility(
            ChangeProjectVisibility(
                project_id=project_id,
                expected_version=updated.version,
                visibility=visibility,
            )
        )
    _tagged(response, updated.version)
    return updated


@projects.post("/{project_id}/status", response_model=ProjectResource)
def change_project_status(
    project_id: uuid.UUID,
    body: ProjectStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    _load_project(session, principal, project_id)
    updated = ProjectService(_context(session, principal, actor)).change_status(
        ChangeProjectStatus(
            project_id=project_id, expected_version=expected, target=body.target
        )
    )
    _tagged(response, updated.version)
    return updated


# --------------------------------------------------------------------------- milestone


@projects.post(
    "/{project_id}/milestones",
    status_code=status.HTTP_201_CREATED,
    response_model=MilestoneResource,
)
def create_milestone(
    project_id: uuid.UUID,
    body: MilestoneCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint=f"POST /api/v1/projects/{project_id}/milestones",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    _load_project(session, principal, project_id)
    milestone = MilestoneService(_context(session, principal, actor)).create(
        CreateMilestone(project_id=project_id, **body.model_dump())
    )
    guard.record(201, MilestoneResource.model_validate(milestone).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/milestones/{milestone.id}"
    _tagged(response, milestone.version)
    return milestone


@projects.get("/{project_id}/milestones", response_model=MilestoneList)
def list_project_milestones(
    project_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> MilestoneList:
    _load_project(session, principal, project_id)
    return MilestoneList(
        items=[
            MilestoneResource.model_validate(item)
            for item in list_readable_milestones(session, principal, project_id=project_id)
        ]
    )


@milestones.get("/{milestone_id}", response_model=MilestoneResource)
def read_milestone(
    milestone_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> Any:
    milestone = _load_milestone(session, principal, milestone_id)
    _tagged(response, milestone.version)
    return milestone


@milestones.patch("/{milestone_id}", response_model=MilestoneResource)
def update_milestone(
    milestone_id: uuid.UUID,
    body: MilestoneUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    _load_milestone(session, principal, milestone_id)
    updated = MilestoneService(_context(session, principal, actor)).update(
        UpdateMilestone(
            milestone_id=milestone_id,
            expected_version=expected,
            **body.model_dump(exclude_unset=True),
        )
    )
    _tagged(response, updated.version)
    return updated


@milestones.post("/{milestone_id}/status", response_model=MilestoneResource)
def change_milestone_status(
    milestone_id: uuid.UUID,
    body: MilestoneStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    _load_milestone(session, principal, milestone_id)
    updated = MilestoneService(_context(session, principal, actor)).change_status(
        ChangeMilestoneStatus(
            milestone_id=milestone_id, expected_version=expected, target=body.target
        )
    )
    _tagged(response, updated.version)
    return updated
