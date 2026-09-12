"""`/api/v1/dependencies`.

The collection is top level because a Dependency is polymorphic: either endpoint may be a Work or a
Milestone, so there is no single parent to nest it under. The per-item view is nested
(`/work/{id}/dependencies`) because "what is holding this up" is a question about one item.

A rejected cycle returns the path it would have closed. `find_cycle` has returned the path rather
than a boolean since Checkpoint 2 for exactly this: the difference between "that would create a
cycle" and "A blocks B blocks C blocks A" is the difference between an error a person can act on
and one they can only stare at. Both endpoints are in the caller's organization (BR-D-04 forbids
anything else), so the path reveals nothing they could not already ask for.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    DependencyCreateRequest,
    DependencyList,
    DependencyResource,
)
from app.contexts.work.public import (
    ChangeDependencyStatus,
    CreateDependency,
    DependencyService,
    DependencyStatus,
    EntityNotFound,
    ServiceContext,
    list_dependencies_of_work,
)
from app.contexts.work.public import get_dependency as get_readable_dependency
from app.contexts.work.public import get_work as get_readable_work
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    PrincipalDep,
    SessionDep,
)
from app.platform.http.etag import etag_for, require_if_match
from app.platform.http.idempotency import Idempotency, replay_response

router = APIRouter(
    prefix="/api/v1/dependencies", tags=["dependencies"], responses=PROBLEM_RESPONSES
)
work_scoped = APIRouter(prefix="/api/v1/work", tags=["dependencies"], responses=PROBLEM_RESPONSES)

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _context(session: SessionDep, principal: PrincipalDep, actor: ActorDep) -> ServiceContext:
    return ServiceContext(session=session, principal=principal, actor=actor)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DependencyResource)
def create_dependency(
    body: DependencyCreateRequest,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint="POST /api/v1/dependencies",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    dependency = DependencyService(_context(session, principal, actor)).create(
        CreateDependency(**body.model_dump())
    )
    guard.record(201, DependencyResource.model_validate(dependency).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/dependencies/{dependency.id}"
    response.headers["ETag"] = etag_for(dependency.version)
    return dependency


@router.post("/{dependency_id}/withdraw", response_model=DependencyResource)
def withdraw_dependency(
    dependency_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    # Withdrawn, never deleted (BR-D-05). A dependency that existed is part of why the schedule
    # looked the way it did, and removing the row would remove the explanation with it.
    expected = require_if_match(if_match)
    if get_readable_dependency(session, principal, dependency_id) is None:
        raise EntityNotFound("dependency", dependency_id)
    withdrawn = DependencyService(_context(session, principal, actor)).change_status(
        ChangeDependencyStatus(
            dependency_id=dependency_id,
            expected_version=expected,
            target=DependencyStatus.WITHDRAWN,
        )
    )
    response.headers["ETag"] = etag_for(withdrawn.version)
    return withdrawn


@work_scoped.get("/{work_id}/dependencies", response_model=DependencyList)
def list_work_dependencies(
    work_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> DependencyList:
    """Both directions in one list: what blocks this Work, and what it blocks."""
    if get_readable_work(session, principal, work_id) is None:
        raise EntityNotFound("work", work_id)
    return DependencyList(
        items=[
            DependencyResource.model_validate(item)
            for item in list_dependencies_of_work(session, principal, work_id=work_id)
        ]
    )
