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

from fastapi import APIRouter, Query, Response, status

import app.contexts.intelligence.public as intelligence
import app.contexts.signal.public as signal
from app.agent.providers.fake import FakeProvider
from app.agent.runtime import AgentRuntime
from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    AIInteractionDetail,
    AIInteractionList,
    AIInteractionResource,
    AnalysisResource,
    ToolCallResource,
)
from app.platform.actor import Actor
from app.platform.authz import Principal
from app.platform.authz.agent import AgentCapability, AgentIdentity, AgentPrincipal
from app.platform.errors import EntityNotFound
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    ObjectStoreDep,
    PrincipalDep,
    SessionDep,
)
from app.platform.http.idempotency import Idempotency, replay_response

router = APIRouter(prefix="/api/v1", tags=["agent"], responses=PROBLEM_RESPONSES)

#: The agents this deployment knows about.
#:
#: A registry rather than a database table, for now and deliberately: an agent's capabilities are a
#: property of the code that implements it, and making them editable at runtime would be a way to
#: widen an agent's reach without a deployment. When agents become configurable per organization,
#: the policy half moves to a table and this stays as the ceiling.
AGENTS: dict[str, AgentIdentity] = {
    "extractor": AgentIdentity(
        name="extractor",
        capabilities=frozenset({AgentCapability.EXTRACT}),
    ),
}

#: BR-AI-30's policy, keyed per organization and capability once it is configurable. Until then the
#: MVP default: extraction may propose Work and Commitments, and nothing else is reachable.
DEFAULT_POLICY = frozenset({"create_work", "create_commitment"})


class _RuntimeServices:
    """What the runtime is allowed to reach. Holds the session; exposes no way to it.

    Every method is a published service call carrying the runtime's own `Actor`, so the AI origin
    travels with the write rather than being asserted separately (ADR-0043).
    """

    def __init__(
        self,
        session: SessionDep,
        principal: Principal,
        store: ObjectStoreDep,
    ) -> None:
        self._session = session
        self._principal = principal
        self._store = store
        self._sequence = 0

    def record_interaction(self, **fields: Any) -> uuid.UUID:
        return intelligence.start_interaction(
            self._session,
            org_id=self._principal.org_id,
            command=intelligence.StartInteraction(**fields),
        ).id

    def finish_interaction(self, interaction_id: uuid.UUID, **fields: Any) -> None:
        intelligence.finish_interaction(
            self._session,
            org_id=self._principal.org_id,
            interaction_id=interaction_id,
            **fields,
        )

    def record_tool_call(self, **fields: Any) -> None:
        intelligence.record_tool_call(
            self._session, org_id=self._principal.org_id, **fields
        )

    def read_event(self, event_id: uuid.UUID) -> signal.Event | None:
        # Read-filtered: the agent sees what the delegating person sees, restricted Events
        # included — which is to say, not included (BR-E-08).
        return signal.read_event(self._session, self._principal, event_id)

    def create_evidence(
        self, actor: Actor, command: signal.CreateEvidence
    ) -> signal.Evidence:
        return signal.EvidenceService(
            signal.ServiceContext(
                session=self._session,
                principal=self._principal,
                actor=actor,
                object_store=self._store,
            )
        ).create(command)

    def raise_proposal(
        self, actor: Actor, command: intelligence.RaiseProposal
    ) -> intelligence.Proposal:
        return intelligence.ProposalService(
            intelligence.ServiceContext(
                session=self._session, principal=self._principal, actor=actor
            )
        ).raise_proposal(command)


@router.post(
    "/events/{event_id}/analyze",
    status_code=status.HTTP_201_CREATED,
    response_model=AnalysisResource,
)
def analyze_event(
    event_id: uuid.UUID,
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
    identity = AGENTS.get(agent)
    if identity is None:
        raise EntityNotFound("agent", uuid.UUID(int=0))

    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint=f"POST /api/v1/events/{event_id}/analyze",
        key=idempotency_key,
        payload={"agent": agent},
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    # The intersection (BR-AI-03): this agent's capabilities, the caller's own authority, and the
    # organization policy. The delegating principal is the authenticated caller — not a field.
    agent_principal = AgentPrincipal(
        identity=identity, delegated=principal, policy_allows=DEFAULT_POLICY
    )
    result = AgentRuntime(FakeProvider()).analyze_event(
        _RuntimeServices(session, principal, store),
        agent_principal,
        event_id,
        routed_to_person_id=actor.person_id,
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
