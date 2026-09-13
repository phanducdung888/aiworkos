"""Commitments: promises people made, recorded as they made them.

A Commitment is not an assignment. A WorkAssignment says the organization decided somebody is
responsible; a Commitment says a person said they would do a thing. The two can describe one
intention and remain different facts about it, which is why `fulfilling_work_id` links them without
merging them (BR-C-08).

Two endpoints look like conveniences and are not. `POST /{id}/status` is the only way a Commitment
moves, because BR-C-04 is a transition table and a PATCH of a status field would let a caller skip
states. And there is no way to change `due_date` through the update endpoint: moving a deadline is a
renegotiation (BR-C-07), which requires a new date and retains the old one, so it goes through the
status path where it can be recorded as what it is.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    CommitmentCreate,
    CommitmentList,
    CommitmentResource,
    CommitmentStatusChange,
    CommitmentUpdate,
)
from app.contexts.commitment.public import (
    ChangeCommitmentStatus,
    CommitmentFilter,
    CommitmentService,
    CommitmentStatus,
    CreateCommitment,
    ServiceContext,
    UpdateCommitment,
    get_commitment,
    list_commitments,
)
from app.platform.authz import Action, ResourceType
from app.platform.errors import EntityNotFound
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    PrincipalDep,
    SessionDep,
    may_reach,
)
from app.platform.http.etag import etag_for, require_if_match
from app.platform.http.idempotency import Idempotency, replay_response
from app.platform.http.pagination import clamp_limit, decode_cursor

router = APIRouter(
    prefix="/api/v1/commitments", tags=["commitment"], responses=PROBLEM_RESPONSES
)

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _context(session: SessionDep, principal: PrincipalDep, actor: ActorDep) -> ServiceContext:
    # `leads_team_ids` is empty here and filled by the reach computation when BR-C-09's
    # management-chain arm is needed. Today the matrix's DEPT and TEAM grants carry it, so a lead
    # reaches a commitment through their grant rather than through this field.
    return ServiceContext(session=session, principal=principal, actor=actor)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CommitmentResource)
def create_commitment(
    body: CommitmentCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """Created `captured`. A promise starts unconfirmed whoever wrote it down (BR-C-04)."""
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint="POST /api/v1/commitments",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    commitment = CommitmentService(_context(session, principal, actor)).create(
        CreateCommitment(**body.model_dump())
    )
    guard.record(201, CommitmentResource.model_validate(commitment).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/commitments/{commitment.id}"
    response.headers["ETag"] = etag_for(commitment.version)
    return commitment


@router.get("", response_model=CommitmentList)
def list_all_commitments(
    session: SessionDep,
    principal: PrincipalDep,
    commitment_status: Annotated[CommitmentStatus | None, Query(alias="status")] = None,
    committed_by_person_id: uuid.UUID | None = None,
    committed_to_person_id: uuid.UUID | None = None,
    fulfilling_work_id: uuid.UUID | None = None,
    origin_event_id: uuid.UUID | None = None,
    due_before: dt.date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> CommitmentList:
    # The matrix, before a row is selected. Scoping to the organization is not
    # authorization: until CP24 a role with no grant on this resource type reached all
    # of it, because every role that could read anything could read it organization-wide
    # and the two questions had never given different answers.
    may_reach(principal, Action.LIST, ResourceType.COMMITMENT)
    page = list_commitments(
        session,
        principal,
        filters=CommitmentFilter(
            status=commitment_status,
            committed_by_person_id=committed_by_person_id,
            committed_to_person_id=committed_to_person_id,
            fulfilling_work_id=fulfilling_work_id,
            origin_event_id=origin_event_id,
            due_before=due_before,
        ),
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return CommitmentList(
        items=[CommitmentResource.model_validate(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{commitment_id}", response_model=CommitmentResource)
def read_commitment(
    commitment_id: uuid.UUID, response: Response, session: SessionDep, principal: PrincipalDep
) -> Any:
    may_reach(principal, Action.READ, ResourceType.COMMITMENT)
    commitment = get_commitment(session, principal, commitment_id)
    if commitment is None:
        raise EntityNotFound("commitment", commitment_id)
    response.headers["ETag"] = etag_for(commitment.version)
    return commitment


@router.patch("/{commitment_id}", response_model=CommitmentResource)
def update_commitment(
    commitment_id: uuid.UUID,
    body: CommitmentUpdate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    expected = require_if_match(if_match)
    updated = CommitmentService(_context(session, principal, actor)).update(
        UpdateCommitment(
            commitment_id=commitment_id,
            expected_version=expected,
            **body.model_dump(exclude_unset=True),
        )
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


@router.post("/{commitment_id}/status", response_model=CommitmentResource)
def change_commitment_status(
    commitment_id: uuid.UUID,
    body: CommitmentStatusChange,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """BR-C-04's transitions, and BR-C-09's question of who may make them."""
    expected = require_if_match(if_match)
    updated = CommitmentService(_context(session, principal, actor)).change_status(
        ChangeCommitmentStatus(
            commitment_id=commitment_id,
            expected_version=expected,
            target=body.target,
            new_due_date=body.new_due_date,
        )
    )
    response.headers["ETag"] = etag_for(updated.version)
    return updated


@router.get("/overdue/today", response_model=CommitmentList)
def overdue_commitments(
    session: SessionDep, principal: PrincipalDep, actor: ActorDep
) -> CommitmentList:
    """BR-C-06 as a question, never as a side effect.

    Returns what would be missed. Nothing is written: `missed` is a transition with an actor and an
    audit entry, and a read that performed it would make a status change appear from nowhere.
    Vague-precision promises are absent by rule — they age into a review prompt instead.
    """
    may_reach(principal, Action.LIST, ResourceType.COMMITMENT)
    return CommitmentList(
        items=[
            CommitmentResource.model_validate(row)
            for row in CommitmentService(_context(session, principal, actor)).overdue_today()
        ]
    )
