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

import app.contexts.commitment.public as commitment
import app.contexts.identity.public as identity
import app.contexts.intelligence.public as intelligence
import app.contexts.signal.public as signal
import app.contexts.work.public as work
from app.agent.providers.errors import ProviderError
from app.agent.providers.fake import FakeProvider
from app.agent.runtime import (
    DEFAULT_CONFIDENCE_POLICY,
    AgentRuntime,
    SubmissionResult,
)
from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    AIInteractionDetail,
    AIInteractionList,
    AIInteractionResource,
    AnalysisResource,
    CapabilityPolicyList,
    CapabilityPolicyResource,
    CapabilityPolicySet,
    ToolCallResource,
)
from app.platform.actor import Actor
from app.platform.agentkit.confidence import ConfidencePolicy
from app.platform.agentkit.contract import AgentAnalysis, IntentKind, PersonReference
from app.platform.authz import Action, Principal, ResourceType, authorize
from app.platform.authz.agent import AgentCapability, AgentIdentity, AgentPrincipal
from app.platform.authz.model import ResourceRef
from app.platform.errors import EntityNotFound, UpstreamProviderError
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

#: No default policy constant. It used to live here and that was the Checkpoint 8 gap: every
#: organization shared one hard-coded set, so turning extraction off for one customer was a
#: deployment. The policy is now rows in `agent_capability_policy`, read per request, and an
#: organization with no rows denies everything (ADR-0047).


#: What each duplicate search is recorded as having been given. Redacted like every other
#: `tool_call` argument set: the keys, never the values (BR-AI-31).
_DUPLICATE_SEARCH_KEYS: dict[str, list[str]] = {
    "find_similar_work": ["title"],
    "find_similar_commitments": ["statement", "committed_by_person_id"],
}

#: Said in the language of the thing that already exists. "Similar work already exists" on a
#: commitment refusal was how the category error stayed invisible for three checkpoints.
_DUPLICATE_REFUSAL: dict[str, str] = {
    "find_similar_work": "similar work already exists",
    "find_similar_commitments": "this person has already made a similar commitment",
}


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
        agent_principal: AgentPrincipal,
        confidence_policy: ConfidencePolicy,
    ) -> None:
        self._session = session
        self._principal = principal
        self._store = store
        # Held here, not passed to the runtime: the agent must not be able to read the policy it
        # is constrained by, let alone influence it (ADR-0052).
        self._agent_principal = agent_principal
        self._confidence_policy = confidence_policy

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

    def find_similar_work(self, title: str) -> list[work.SimilarWork]:
        """BR-AI-05, through the caller's own visibility.

        The reach is the *delegating person's*, so an agent cannot discover Work its delegate
        cannot see. A similarity search is an excellent way to leak a corpus one probe at a time,
        and the defence is to never select the rows rather than to filter them afterwards.
        """
        return work.find_similar_work(
            self._session,
            self._principal,
            work.reach_of(self._session, self._principal),
            title=title,
        )

    def find_similar_commitments(
        self, statement: str, committed_by_person_id: uuid.UUID
    ) -> list[commitment.SimilarCommitment]:
        """BR-AI-05 for a promise, which is a different question from BR-AI-05 for a job.

        Scoped to the committer, because "has this already been promised" is only answerable about
        somebody. Commitment read visibility is organization-wide by that context's own decision,
        so there is no corpus here the delegating person could not already list.
        """
        return commitment.find_similar_commitments(
            self._session,
            self._principal,
            statement=statement,
            committed_by_person_id=committed_by_person_id,
        )

    def resolved_participants(self, event_id: uuid.UUID) -> tuple[PersonReference, ...]:
        """The people this Event already resolved, as references the agent may point at.

        Only `participant_id` and `role` cross to the agent — never a `person_id`. An agent cannot
        name a Person because it is never given one, which is how BR-AI-34 stops being a rule
        somebody has to remember (ADR-0052).
        """
        return tuple(
            PersonReference(participant_id=row.id, role=row.role)
            for row in signal.participants_for(
                self._session, org_id=self._principal.org_id, event_id=event_id
            )
        )

    def submit_analysis(
        self,
        actor: Actor,
        event: signal.Event,
        analysis: AgentAnalysis,
        *,
        routed_to_person_id: uuid.UUID,
    ) -> SubmissionResult:
        """Validate the agent's intents, then raise Proposals for the ones that survive.

        This is the composition point for the whole boundary: the validator lives in Intelligence
        where the agent cannot reach it, and the Evidence and Proposal services are called from
        here rather than from the runtime.
        """
        validator = intelligence.IntentValidator(
            self._agent_principal,
            participants=self._resolved(event.id),
            confidence_policy=self._confidence_policy,
            # The Event as WorkOS knows it. `occurred_at` anchors every deadline the validator
            # reads, so re-analysing an old message produces the dates it produced the first time
            # rather than dates relative to whenever the re-run happened (ADR-0055).
            source=intelligence.SourceEvent(
                body_text=event.body_text or "",
                occurred_at=event.occurred_at,
                timezone=self._organization_timezone(),
            ),
        )
        outcome = validator.validate(analysis)

        sequence = 0
        for intent, reason in outcome.refused:
            sequence += 1
            # A refusal is the authority model working and is the row worth reading. Recorded
            # before anything is written, so a run that refuses everything still explains itself.
            self.record_tool_call(
                ai_interaction_id=actor.ai_interaction_id,
                sequence=sequence,
                tool_name=intent.kind.value,
                tool_version="v1",
                arguments_redacted={"keys": sorted(intent.arguments)},
                authorization_result="denied",
                outcome="refused",
                error=reason[:2000],
            )

        evidence_ids: list[uuid.UUID] = []
        proposal_ids: list[uuid.UUID] = []
        duplicates = 0
        for accepted in outcome.accepted:
            sequence += 1
            # BR-AI-05, on the WorkOS side of the boundary: the agent does not get to decide
            # whether it looked. *What* it looks in is decided by what the intent would create —
            # a promise is not a duplicate because a Work item is worded alike, which is what this
            # used to conclude.
            search, similar_ids = self._duplicate_search(accepted)
            self.record_tool_call(
                ai_interaction_id=actor.ai_interaction_id,
                sequence=sequence,
                tool_name=search,
                tool_version="v1",
                arguments_redacted={"keys": _DUPLICATE_SEARCH_KEYS[search]},
                authorization_result="allowed",
                outcome="succeeded",
            )
            sequence += 1
            if similar_ids:
                self.record_tool_call(
                    ai_interaction_id=actor.ai_interaction_id,
                    sequence=sequence,
                    tool_name=accepted.tool,
                    tool_version="v1",
                    arguments_redacted={
                        "keys": sorted(accepted.arguments),
                        "similar_to": [str(row) for row in similar_ids[:3]],
                    },
                    authorization_result="allowed",
                    outcome="refused",
                    error=f"{_DUPLICATE_REFUSAL[search]} (BR-AI-05)",
                )
                duplicates += 1
                continue

            evidence = self._create_evidence(actor, event, accepted)
            evidence_ids.append(evidence.id)
            proposal = self._raise_proposal(
                actor, event, accepted, evidence.id, routed_to_person_id
            )
            proposal_ids.append(proposal.id)
            self.record_tool_call(
                ai_interaction_id=actor.ai_interaction_id,
                sequence=sequence,
                tool_name=accepted.tool,
                tool_version="v1",
                arguments_redacted={"keys": sorted(accepted.arguments)},
                authorization_result="allowed",
                # The Proposal was raised; the tool has not run and will not until somebody
                # approves it.
                outcome="not_attempted",
                target_entity_type=accepted.target_type,
            )

        return SubmissionResult(
            evidence_ids=tuple(evidence_ids),
            proposal_ids=tuple(proposal_ids),
            refused=len(outcome.refused),
            unresolved_attribution=sum(
                1 for _, reason in outcome.refused if "BR-AI-34" in reason
            ),
            duplicates_skipped=duplicates,
        )

    def _duplicate_search(self, accepted: Any) -> tuple[str, list[uuid.UUID]]:
        """Which corpus answers "does this already exist" for this intent, and what it found.

        Returns the search's registry name as well as its hits, because the `tool_call` row has to
        record *which* question was asked. An audit that says "looked for duplicates" without
        saying where is not evidence that BR-AI-05 was satisfied.

        A commitment is searched among that person's standing promises and never among Work titles.
        Anything that is neither is searched as Work, which is the conservative default: an unknown
        intent kind gets a duplicate check rather than a free pass.
        """
        if accepted.kind is IntentKind.CREATE_COMMITMENT:
            # Put there by the validator from a resolved participant, never by the agent
            # (BR-AI-34): an intent that reached here without one does not exist.
            committer = accepted.arguments["committed_by_person_id"]
            return "find_similar_commitments", [
                row.id
                for row in self.find_similar_commitments(
                    accepted.summary, uuid.UUID(str(committer))
                )
            ]
        return "find_similar_work", [row.id for row in self.find_similar_work(accepted.summary)]

    def _organization_timezone(self) -> str:
        """The calendar "Friday" is read against. Falls back to UTC, which is the column default."""
        organization = identity.get_organization(self._session, org_id=self._principal.org_id)
        return organization.timezone if organization is not None else "UTC"

    def _resolved(
        self, event_id: uuid.UUID
    ) -> tuple[intelligence.ResolvedParticipant, ...]:
        """The same participants, with their person ids — for the validator, not for the agent."""
        return tuple(
            intelligence.ResolvedParticipant(
                participant_id=row.id, person_id=row.person_id, role=row.role
            )
            for row in signal.participants_for(
                self._session, org_id=self._principal.org_id, event_id=event_id
            )
        )

    def _create_evidence(
        self, actor: Actor, event: signal.Event, accepted: Any
    ) -> signal.Evidence:
        return signal.EvidenceService(
            signal.ServiceContext(
                session=self._session,
                principal=self._principal,
                actor=actor,
                object_store=self._store,
            )
        ).create(
            signal.CreateEvidence(
                event_id=event.id,
                target_type=accepted.target_type,
                target_id=uuid.uuid4(),
                assertion="creates",
                locator=signal.TextLocator(
                    char_start=accepted.char_start, char_end=accepted.char_end
                ),
                # Verbatim, sliced from the Event itself rather than from the agent's summary:
                # BR-E-05 checks it against the source and a paraphrase would be refused.
                excerpt=(event.body_text or "")[
                    accepted.char_start : accepted.char_end
                ],
                confidence=accepted.confidence,
            )
        )

    def _raise_proposal(
        self,
        actor: Actor,
        event: signal.Event,
        accepted: Any,
        evidence_id: uuid.UUID,
        routed_to_person_id: uuid.UUID,
    ) -> intelligence.Proposal:
        arguments = dict(accepted.arguments)
        if accepted.kind is IntentKind.CREATE_COMMITMENT:
            # BR-C-03. The citation travels into the action, so the Commitment that eventually
            # executes can show where the promise was made.
            arguments["evidence_ids"] = [str(evidence_id)]
            # And the message it was made in. Added here rather than allow-listed for the agent
            # (BR-AI-34, ADR-0052): WorkOS knows which Event it handed the runtime, and an agent
            # that could name a source Event could cite a conversation it never read.
            arguments["origin_event_id"] = str(event.id)
        return intelligence.ProposalService(
            intelligence.ServiceContext(
                session=self._session, principal=self._principal, actor=actor
            )
        ).raise_proposal(
            intelligence.RaiseProposal(
                kind=intelligence.ProposalKind.CREATE,
                target_type=accepted.target_type,
                summary=accepted.summary,
                reason=accepted.rationale,
                tool=accepted.tool,
                tool_version="v1",
                arguments=arguments,
                routed_to_person_id=routed_to_person_id,
                confidence=accepted.confidence,
                source_event_id=event.id,
                evidence_ids=(evidence_id,),
            )
        )


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
    # organization's policy read from the database. The delegating principal is the authenticated
    # caller — never a field — and an organization that has decided nothing denies everything.
    agent_principal = AgentPrincipal(
        identity=identity,
        delegated=principal,
        policy=intelligence.load_capability_policy(session, org_id=principal.org_id),
    )
    try:
            result = AgentRuntime(_provider_for(request)).analyze_event(
            _RuntimeServices(
                session, principal, store, agent_principal, DEFAULT_CONFIDENCE_POLICY
            ),
            agent_principal,
            event_id,
            routed_to_person_id=actor.person_id,
        )
    except ProviderError as error:
        # Caught rather than allowed to escape, so the interaction the runtime already marked
        # `failed` is committed with the request. Letting it raise would roll the transaction back
        # and lose the record of a run that genuinely happened — and a provider outage would then
        # be invisible rather than merely unsuccessful (ADR-0049).
        raise UpstreamProviderError(
            f"{type(error).__name__}: {error}", retryable=error.retryable
        ) from error
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


@router.get("/agent-policy", response_model=CapabilityPolicyList)
def read_capability_policy(
    session: SessionDep, principal: PrincipalDep
) -> CapabilityPolicyList:
    """What this organization has decided its agents may do.

    Readable organization-wide: somebody asked to review an AI-raised Proposal is entitled to know
    what the AI was permitted to do in the first place.
    """
    return CapabilityPolicyList(
        items=[
            CapabilityPolicyResource.model_validate(row)
            for row in intelligence.capability_policy_rows(
                session, org_id=principal.org_id
            )
        ]
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
