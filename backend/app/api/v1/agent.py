"""Triggering an analysis, and reading what it did.

`POST /api/v1/events/{id}/analyze` is the Level 1 path: the runtime reads the Event, cites what it
found and raises Proposals. It executes nothing, and there is no endpoint that makes it — an
approved Proposal is queued (ADR-0044) and a worker runs it.

The adapter below is the composition point. The agent layer may not import a session or a
repository (ADR-0045), so something has to hold both the session and the published services and
hand the runtime a narrow interface. That is a router's job — parse, authenticate, delegate — and
this is the delegation, written out rather than hidden behind a factory.

The agent principal is built here from the **authenticated caller**: the human making the request
is the delegating person, and the agent identity comes from the registry, never from the body. A
request cannot name its own agent, its own capabilities or its own authority (ADR-0043).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status

import app.contexts.intelligence.public as intelligence
import app.workers.analysis as analysis
from app.agent.providers.fake import FakeProvider
from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    AIInteractionDetail,
    AIInteractionList,
    AIInteractionResource,
    AnalysisResource,
    CapabilityPolicyList,
    CapabilityPolicyResource,
    CapabilityPolicySet,
    PolicyCellResource,
    ToolCallResource,
)
from app.platform.authz import Action, ResourceType, authorize
from app.platform.authz.agent import (
    tools_for_cell,
)
from app.platform.authz.model import ResourceRef
from app.platform.errors import EntityNotFound
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    ObjectStoreDep,
    PrincipalDep,
    SessionDep,
    may_reach,
)
from app.platform.http.idempotency import Idempotency, replay_response

router = APIRouter(prefix="/api/v1", tags=["agent"], responses=PROBLEM_RESPONSES)

#: The agents this deployment knows about.
#:
#: A registry rather than a database table, for now and deliberately: an agent's capabilities are a
#: property of the code that implements it, and making them editable at runtime would be a way to
#: widen an agent's reach without a deployment. When agents become configurable per organization,
#: the policy half moves to a table and this stays as the ceiling.
def _provider_for(request: Request) -> Any:
    """The provider this deployment uses, or whatever the app was built with.

    `FakeProvider` unless something explicitly installs another — the real adapter is never on a
    default path (ADR-0049). Tests replace it on the app rather than by patching a module global,
    so a test that forgets to undo the replacement cannot leak into the next one.
    """
    override = getattr(request.app.state, "llm_provider", None)
    if override is not None:
        return override
    return FakeProvider()



@router.post(
    "/events/{event_id}/analyze",
    status_code=status.HTTP_201_CREATED,
    response_model=AnalysisResource,
)
def analyze_event(
    event_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    store: ObjectStoreDep,
    agent: str = "extractor",
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    """Level 1: observe, interpret, propose. Nothing is executed.

    The response lists what was raised so a caller can go and review it. It does not contain the
    model's raw output, which is not persisted anywhere.
    """
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint=f"POST /api/v1/events/{event_id}/analyze",
        key=idempotency_key,
        payload={"agent": agent},
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    # The same call the queue makes (ADR-0069). Authorization, the capability intersection and the
    # provider-error translation all live in there, so a request and a job cannot drift apart.
    result = analysis.analyse(
        session,
        principal=principal,
        routed_to_person_id=actor.person_id,
        store=store,
        provider=_provider_for(request),
        event_id=event_id,
        agent=agent,
    )
    resource = AnalysisResource(
        ai_interaction_id=result.interaction_id,
        evidence_ids=list(result.evidence_ids),
        proposal_ids=list(result.proposal_ids),
        low_confidence=result.low_confidence,
    )
    guard.record(201, resource.model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/ai-interactions/{result.interaction_id}"
    return resource


@router.get("/ai-interactions", response_model=AIInteractionList)
def list_ai_interactions(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> AIInteractionList:
    # An AIInteraction has no row in the matrix: it is the record of a run that produced
    # Proposals, so it is gated on the same grant those are. Reading it is reading how a
    # Proposal came to exist — which prompt, which model, what the model was given.
    may_reach(principal, Action.LIST, ResourceType.PROPOSAL)
    return AIInteractionList(
        items=[
            AIInteractionResource.model_validate(row)
            for row in intelligence.list_interactions(
                session, org_id=principal.org_id, limit=limit
            )
        ]
    )


@router.get("/ai-interactions/{interaction_id}", response_model=AIInteractionDetail)
def read_ai_interaction(
    interaction_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Any:
    """One run, with every tool call it made — including the refused ones."""
    may_reach(principal, Action.READ, ResourceType.PROPOSAL)
    interaction = intelligence.get_interaction(
        session, org_id=principal.org_id, interaction_id=interaction_id
    )
    if interaction is None:
        raise EntityNotFound("ai_interaction", interaction_id)
    detail = AIInteractionDetail.model_validate(interaction)
    detail.tool_calls = [
        ToolCallResource.model_validate(row)
        for row in intelligence.calls_for(
            session, org_id=principal.org_id, interaction_id=interaction_id
        )
    ]
    return detail


@router.post("/approvals/{approval_id}/queue", status_code=status.HTTP_202_ACCEPTED)
def queue_approved_execution(
    approval_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> dict[str, Any]:
    """Hand an approved action to the worker instead of running it in this request (ADR-0044).

    202, not 200: the mutation has not happened yet and saying otherwise would be a lie the client
    would build on. Enqueueing twice is idempotent on the approval, so a retry is safe.
    """
    record = intelligence.approval_for_id(session, principal, approval_id)
    if record is None:
        raise EntityNotFound("approval_record", approval_id)
    queued = intelligence.enqueue_execution(
        session, org_id=principal.org_id, approval_id=approval_id
    )
    return {
        "approval_id": str(approval_id),
        "queued": queued,
        "queued_at": dt.datetime.now(dt.UTC).isoformat(),
    }


@router.get("/agent-policy", response_model=CapabilityPolicyList)
def read_capability_policy(
    session: SessionDep, principal: PrincipalDep
) -> CapabilityPolicyList:
    """What this organization has decided its agents may do.

    Readable organization-wide: somebody asked to review an AI-raised Proposal is entitled to know
    what the AI was permitted to do in the first place.
    """
    # Organization-wide among the roles that hold the grant, which is not the same as everyone. A
    # connector holds none of it (ADR-0060) and read the whole policy until CP24, because this
    # handler scoped to the tenant and asked the matrix nothing. Said here rather than in the
    # docstring, which is published as the endpoint's description.
    may_reach(principal, Action.LIST, ResourceType.AGENT_CAPABILITY_POLICY)
    return CapabilityPolicyList(
        items=[
            CapabilityPolicyResource.model_validate(row)
            for row in intelligence.capability_policy_rows(
                session, org_id=principal.org_id
            )
        ],
        # The grid as well as the decisions. An editor that had to infer the surface from the
        # decided rows could only ever offer what was already decided, which is the one thing an
        # administrator does not need help with (ADR-0047).
        available=[
            PolicyCellResource(
                capability=cell.capability.value,
                entity_type=cell.entity_type,
                action=cell.action.value,
                mode=row.mode if row is not None else cell.mode.value,
                decided=row is not None,
                reason=row.reason if row is not None else None,
                decided_by_person_id=row.decided_by_person_id if row is not None else None,
                updated_at=row.updated_at if row is not None else None,
                version=row.version if row is not None else None,
                tools=sorted(
                    tools_for_cell(cell.capability, cell.entity_type, cell.action)
                ),
            )
            for cell, row in intelligence.capability_policy_surface(
                session, org_id=principal.org_id
            )
        ],
    )


@router.put("/agent-policy", response_model=CapabilityPolicyResource)
def set_capability_policy(
    body: CapabilityPolicySet,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
) -> Any:
    """Set one cell. `org_admin` only, and never reachable by an agent (ADR-0047).

    PUT rather than POST: a cell is set, not created. There is one row per
    `(capability, entity_type, action)` and setting it twice is the same decision made twice, so
    the operation is idempotent and "what is the policy" has exactly one answer.
    """
    decision = authorize(
        principal,
        Action.UPDATE,
        ResourceRef(
            type=ResourceType.AGENT_CAPABILITY_POLICY,
            org_id=principal.org_id,
            id=None,
            relations=frozenset(),
        ),
    )
    return intelligence.set_capability_mode(
        session,
        principal=principal,
        actor=actor,
        decision=decision,
        capability=body.capability,
        entity_type=body.entity_type,
        action=body.action,
        mode=body.mode,
        reason=body.reason,
    )
