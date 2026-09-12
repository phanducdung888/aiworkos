"""`/api/v1/work`.

Thin by contract (§12). Each handler parses the request, hands a command to an application service,
and serialises what comes back. There is no rule here, no query, no authorization decision: the
service authorizes, the domain decides, the query side filters. If a handler below ever needs an
`if` about business meaning, the rule belongs one layer down.

Reads go through `queries`, which authorizes by filtering, so an unreadable Work is
indistinguishable from a missing one — both produce 404 and neither says which (contract §5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    AssignmentCreate,
    AssignmentList,
    AssignmentResource,
    OwnerAssignment,
    WorkCreate,
    WorkList,
    WorkListItem,
    WorkResource,
    WorkStatusChange,
    WorkUpdate,
)
from app.contexts.work.public import AssignmentService as Assignments
from app.contexts.work.public import (
    AssignWork,
    ChangeWorkStatus,
    CreateWork,
    EndAssignment,
    EntityNotFound,
    ServiceContext,
    SetOwner,
    UpdateWork,
    WorkFilter,
    WorkPartition,
    WorkService,
    WorkStatus,
)
from app.contexts.work.public import get_work as get_readable_work
from app.contexts.work.public import list_assignments as list_readable_assignments
from app.contexts.work.public import (
    list_work as list_readable_work,
)
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    PrincipalDep,
    SessionDep,
)
from app.platform.http.etag import etag_for, require_if_match
from app.platform.http.idempotency import Idempotency, replay_response
from app.platform.http.pagination import clamp_limit, decode_cursor

router = APIRouter(prefix="/api/v1/work", tags=["work"], responses=PROBLEM_RESPONSES)

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _context(session: SessionDep, principal: PrincipalDep, actor: ActorDep) -> ServiceContext:
    return ServiceContext(session=session, principal=principal, actor=actor)


def _tagged(response: Response, version: int) -> None:
    response.headers["ETag"] = etag_for(version)


def _load(session: SessionDep, principal: PrincipalDep, work_id: uuid.UUID) -> Any:
    """Read-authorized load. A Work the caller may not read does not exist as far as they know."""
    work = get_readable_work(session, principal, work_id)
    if work is None:
        raise EntityNotFound("work", work_id)
    return work


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkResource)
def create_work(
    body: WorkCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint="POST /api/v1/work",
        key=idempotency_key,
        payload=body.model_dump(mode="json", exclude_unset=True),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    work = WorkService(_context(session, principal, actor)).create(
        # `exclude_unset` so an omitted visibility stays UNSET and is inherited from the Project,
        # rather than arriving as an explicit `team` that would silently narrow it (BR-W-19).
        CreateWork(**body.model_dump(exclude_unset=True))
    )
    guard.record(201, WorkResource.model_validate(work).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/work/{work.id}"
    _tagged(response, work.version)
    return work


@router.get("", response_model=WorkList)
def list_work(
    session: SessionDep,
    principal: PrincipalDep,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    work_status: Annotated[WorkStatus | None, Query(alias="status")] = None,
    partition: Annotated[WorkPartition | None, Query()] = None,
    owner_person_id: Annotated[uuid.UUID | None, Query()] = None,
    due_before: Annotated[dt.date | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> WorkList:
    page = list_readable_work(
        session,
        principal,
        filters=WorkFilter(
            project_id=project_id,
            status=work_status,
            partition=partition,
            owner_person_id=owner_person_id,
            due_before=due_before,
        ),
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return WorkList(
        items=[WorkListItem.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{work_id}", response_model=WorkResource)
def read_work(
    work_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> Any:
    work = _load(session, principal, work_id)
    _tagged(response, work.version)
    return work


@router.patch("/{work_id}", response_model=WorkResource)
def update_work(
    work_id: uuid.UUID,
    body: WorkUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    _load(session, principal, work_id)
    updated = WorkService(_context(session, principal, actor)).update(
        UpdateWork(
            work_id=work_id,
            expected_version=expected,
            # Only the fields the caller actually sent. Everything else stays UNSET and is left
            # alone, which is the difference between a partial update and a silent erasure.
            **body.model_dump(exclude_unset=True),
        )
    )
    _tagged(response, updated.version)
    return updated


@router.post("/{work_id}/status", response_model=WorkResource)
def change_work_status(
    work_id: uuid.UUID,
    body: WorkStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    # A status change mutates an existing entity, so it carries the same precondition a PATCH does.
    # Two people moving one Work at once is ordinary, and resolving it by last-writer-wins is the
    # lost update BR-G-06 exists to prevent.
    expected = require_if_match(if_match)
    _load(session, principal, work_id)
    updated = WorkService(_context(session, principal, actor)).change_status(
        ChangeWorkStatus(
            work_id=work_id,
            expected_version=expected,
            target=body.target,
            blocked_reason=body.blocked_reason,
        )
    )
    _tagged(response, updated.version)
    return updated


@router.post(
    "/{work_id}/assignments",
    status_code=status.HTTP_201_CREATED,
    response_model=AssignmentResource,
)
def assign_work(
    work_id: uuid.UUID,
    body: AssignmentCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    # No If-Match: this creates a new assignment rather than overwriting one, so there is no version
    # the caller could be racing against. The single-active-OWNER rule is the domain's job.
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint=f"POST /api/v1/work/{work_id}/assignments",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    _load(session, principal, work_id)
    assignment = Assignments(_context(session, principal, actor)).assign(
        AssignWork(work_id=work_id, **body.model_dump())
    )
    guard.record(201, AssignmentResource.model_validate(assignment).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/work/{work_id}/assignments/{assignment.id}"
    _tagged(response, assignment.version)
    return assignment


@router.get("/{work_id}/assignments", response_model=AssignmentList)
def list_work_assignments(
    work_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> AssignmentList:
    """Every assignment, ended ones included. History is the point (BR-W-14)."""
    _load(session, principal, work_id)
    return AssignmentList(
        items=[
            AssignmentResource.model_validate(item)
            for item in list_readable_assignments(session, principal, work_id=work_id)
        ]
    )


@router.put("/{work_id}/owner", response_model=AssignmentResource)
def set_work_owner(
    work_id: uuid.UUID,
    body: OwnerAssignment,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
) -> Any:
    """One call, one transaction, whether or not the seat is currently occupied.

    `PUT` because the request states the desired end state rather than an operation: this person is
    the owner. Which matrix cell that needs — `ASSIGN` for unowned Work, `REASSIGN` when somebody is
    being displaced — depends on the current state, and the service decides it. A router that chose
    would be a router implementing authorization policy (contract §3).

    No `If-Match`: the precondition here is about who owns the Work, which the service reads and
    acts on inside one transaction, not about a version the caller read earlier.
    """
    _load(session, principal, work_id)
    assignment = Assignments(_context(session, principal, actor)).set_owner(
        SetOwner(work_id=work_id, person_id=body.person_id, is_primary=body.is_primary)
    )
    response.headers["ETag"] = etag_for(assignment.version)
    return assignment


@router.delete("/{work_id}/assignments/{assignment_id}", response_model=AssignmentResource)
def end_assignment(
    work_id: uuid.UUID,
    assignment_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    # DELETE on the wire, an update in the database. Assignment rows are never removed, because
    # assignment history is how ownership stays attributable (BR-W-14). The response body is the
    # ended row, so a client can see when it ended rather than infer it.
    expected = require_if_match(if_match)
    _load(session, principal, work_id)
    ended = Assignments(_context(session, principal, actor)).end(
        EndAssignment(assignment_id=assignment_id, expected_version=expected)
    )
    if ended.work_id != work_id:
        # The assignment exists but hangs off different Work. Treated as missing rather than as a
        # mismatch, so the URL cannot be used to probe which assignments exist elsewhere.
        raise EntityNotFound("work_assignment", assignment_id)
    _tagged(response, ended.version)
    return ended
