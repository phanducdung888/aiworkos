"""Proposals: reviewable mutations, and the approval that lets one run.

The autonomy ceiling for the MVP is Level 2 (PQ-3): something proposes, a human approves, and the
system executes exactly what was approved. Level 3 — acting without per-action approval — is not
representable here, and that is a property of the schema rather than a setting (BR-AI-31).

Approval and execution are separate, and execution does not happen here at all. `POST
/{id}/decision` writes an immutable ApprovalRecord and changes nothing else; running the approved
action is the queue's job and the worker's (ADR-0048). There is no HTTP path that performs an
approved mutation, which is what makes every future execution control — a rate limit, a window, a
kill switch — something implemented once rather than once per path.

Keeping the two apart is also what makes the binding checkable: between approval and execution
there is a durable record of exactly what a person agreed to, and execution has to match it or not
happen.

Revision is not an edit. `POST /{id}/revise` creates a *new* Proposal superseding the old one,
because a changed action has a different hash and therefore cannot match an approval given for the
original (ADR-0041). A modified action requiring a new approval is structural, not a rule somebody
remembers to apply.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    ApprovalRecordResource,
    ProposalCreate,
    ProposalDecision,
    ProposalDetail,
    ProposalList,
    ProposalResource,
    ProposalRevise,
    ProposedChangeResource,
)
from app.contexts.intelligence.public import (
    DecideProposal,
    ProposalFilter,
    ProposalKind,
    ProposalService,
    ProposalStatus,
    ProposedFieldChange,
    RaiseProposal,
    ReviseProposal,
    ServiceContext,
    approval_for,
    approval_for_id,
    changes_for_proposal,
    evidence_for_proposal,
    get_proposal,
    list_proposals,
)
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

router = APIRouter(prefix="/api/v1", tags=["proposal"], responses=PROBLEM_RESPONSES)

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _context(session: SessionDep, principal: PrincipalDep, actor: ActorDep) -> ServiceContext:
    return ServiceContext(session=session, principal=principal, actor=actor)


def _detail(session: SessionDep, principal: PrincipalDep, proposal: Any) -> ProposalDetail:
    detail = ProposalDetail.model_validate(proposal)
    detail.changes = [
        ProposedChangeResource.model_validate(row)
        for row in changes_for_proposal(session, principal, proposal.id)
    ]
    detail.evidence_ids = evidence_for_proposal(session, principal, proposal.id)
    return detail


@router.post(
    "/proposals", status_code=status.HTTP_201_CREATED, response_model=ProposalDetail
)
def raise_proposal(
    body: ProposalCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """The tool must exist and the arguments must fit it, checked now rather than at execution.

    A Proposal that could never run is worse than none: somebody reads it, approves it, and finds
    out at execution that it was never runnable (ADR-0042).
    """
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint="POST /api/v1/proposals",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    proposal = ProposalService(_context(session, principal, actor)).raise_proposal(
        RaiseProposal(
            kind=body.kind,
            target_type=body.target_type,
            summary=body.summary,
            tool=body.tool,
            tool_version=body.tool_version,
            arguments=body.arguments,
            routed_to_person_id=body.routed_to_person_id,
            target_id=body.target_id,
            reason=body.reason,
            confidence=body.confidence,
            source_event_id=body.source_event_id,
            evidence_ids=tuple(body.evidence_ids),
            changes=tuple(
                ProposedFieldChange(
                    field_path=change.field_path,
                    current_value=change.current_value,
                    proposed_value=change.proposed_value,
                )
                for change in body.changes
            ),
        )
    )
    detail = _detail(session, principal, proposal)
    guard.record(201, detail.model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/proposals/{proposal.id}"
    response.headers["ETag"] = etag_for(proposal.version)
    return detail


@router.get("/proposals", response_model=ProposalList)
def list_all_proposals(
    session: SessionDep,
    principal: PrincipalDep,
    proposal_status: Annotated[ProposalStatus | None, Query(alias="status")] = None,
    kind: ProposalKind | None = None,
    target_type: Annotated[CleanText | None, Query()] = None,
    target_id: uuid.UUID | None = None,
    routed_to_person_id: uuid.UUID | None = None,
    source_event_id: uuid.UUID | None = None,
    resulting_entity_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> ProposalList:
    page = list_proposals(
        session,
        principal,
        filters=ProposalFilter(
            status=proposal_status,
            kind=kind,
            target_type=target_type,
            target_id=target_id,
            routed_to_person_id=routed_to_person_id,
            source_event_id=source_event_id,
            resulting_entity_id=resulting_entity_id,
        ),
        limit=clamp_limit(limit),
        cursor=decode_cursor(cursor) if cursor else None,
    )
    return ProposalList(
        items=[ProposalResource.model_validate(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/proposals/{proposal_id}", response_model=ProposalDetail)
def read_proposal(
    proposal_id: uuid.UUID, response: Response, session: SessionDep, principal: PrincipalDep
) -> Any:
    proposal = get_proposal(session, principal, proposal_id)
    if proposal is None:
        raise EntityNotFound("proposal", proposal_id)
    response.headers["ETag"] = etag_for(proposal.version)
    return _detail(session, principal, proposal)


@router.post(
    "/proposals/{proposal_id}/revise",
    status_code=status.HTTP_201_CREATED,
    response_model=ProposalDetail,
)
def revise_proposal(
    proposal_id: uuid.UUID,
    body: ProposalRevise,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """Returns the replacement. The original becomes `superseded` and is never edited (BR-PR-02)."""
    expected = require_if_match(if_match)
    replacement = ProposalService(_context(session, principal, actor)).revise(
        ReviseProposal(
            proposal_id=proposal_id,
            expected_version=expected,
            summary=body.summary,
            reason=body.reason,
            arguments=body.arguments,
            changes=(
                tuple(
                    ProposedFieldChange(
                        field_path=change.field_path,
                        current_value=change.current_value,
                        proposed_value=change.proposed_value,
                    )
                    for change in body.changes
                )
                if body.changes is not None
                else None
            ),
        )
    )
    response.headers["Location"] = f"/api/v1/proposals/{replacement.id}"
    response.headers["ETag"] = etag_for(replacement.version)
    return _detail(session, principal, replacement)


@router.post(
    "/proposals/{proposal_id}/decision",
    status_code=status.HTTP_201_CREATED,
    response_model=ApprovalRecordResource,
)
def decide_proposal(
    proposal_id: uuid.UUID,
    body: ProposalDecision,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    if_match: IfMatch = None,
) -> Any:
    """Writes the ApprovalRecord. Executes nothing (BR-PR-01)."""
    expected = require_if_match(if_match)
    record = ProposalService(_context(session, principal, actor)).decide(
        DecideProposal(
            proposal_id=proposal_id,
            expected_version=expected,
            decision=body.decision,
            edited_arguments=body.edited_arguments,
            rejection_reason=body.rejection_reason,
        )
    )
    response.headers["Location"] = f"/api/v1/approvals/{record.id}"
    return record


@router.get("/proposals/{proposal_id}/approval", response_model=ApprovalRecordResource)
def read_approval(
    proposal_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Any:
    record = approval_for(session, principal, proposal_id)
    if record is None:
        raise EntityNotFound("approval_record", proposal_id)
    return record


@router.get("/approvals/{approval_id}", response_model=ApprovalRecordResource)
def read_approval_record(
    approval_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Any:
    """One approval, including what its execution produced.

    Addressed by its own id rather than only through its Proposal, because since ADR-0048 the
    execution outcome is written by a worker after the request that approved it has ended — so the
    record is the thing a client polls.
    """
    record = approval_for_id(session, principal, approval_id)
    if record is None:
        raise EntityNotFound("approval_record", approval_id)
    return record
