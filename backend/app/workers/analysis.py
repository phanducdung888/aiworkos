"""Running one analysis, for whoever asked — a person through the API, or nobody at all.

**Why this lives here.** The orchestration used to sit inside the HTTP router, which meant the only
way to analyse a message was to make a web request about it. CP26 needed the job queue to do it
too, and `app.workers` is the highest layer both can import (`api > workers > agent > contexts`).
Nothing about it is worker-specific; it is shared because sharing it is the point.

**Two callers, one code path.** A router that authorised differently from a job would be two
security models with one name. `analyse()` performs the authorization itself rather than trusting
its callers to have done it — which is exactly the hole CP23 found in the router, where an
endpoint's refusal turned out to be incidental rather than checked.

**Whose authority a background run uses.** BR-AI-03 says an agent's authority is the intersection
of its own and the delegating human's, and a queued job has no requester to be that human. The
delegate is the person who *enabled the capability* (ADR-0069): turning it on is the act of
delegation, it is already recorded on the policy row, and if that person loses their role the
analysis stops with a clear refusal instead of running on a ghost.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

import app.contexts.commitment.public as commitment
import app.contexts.identity.public as identity
import app.contexts.intelligence.public as intelligence
import app.contexts.signal.public as signal
import app.contexts.work.public as work
from app.agent.providers.anthropic import AnthropicProvider
from app.agent.providers.errors import ProviderError
from app.agent.providers.fake import FakeProvider
from app.agent.providers.openai import OpenAIProvider
from app.agent.providers.port import LLMProvider
from app.agent.runtime import (
    DEFAULT_CONFIDENCE_POLICY,
    AgentRuntime,
    SubmissionResult,
)
from app.platform import jobs
from app.platform.actor import Actor
from app.platform.agentkit.confidence import ConfidencePolicy
from app.platform.agentkit.contract import AgentAnalysis, IntentKind, PersonReference
from app.platform.authz import Action, Principal, ResourceType, authorize
from app.platform.authz.agent import AgentCapability, AgentIdentity, AgentPrincipal
from app.platform.authz.model import ResourceRef
from app.platform.config import Settings, get_settings
from app.platform.errors import EntityNotFound, UpstreamProviderError
from app.platform.principal import resolve_principal_for
from app.platform.storage import ObjectStore

logger = logging.getLogger(__name__)

#: Re-exported so the worker's handler map reads like the other entry beside it. The name itself
#: belongs to `platform.jobs`, because `signal` enqueues it and `signal` cannot import this module.
ANALYZE_EVENT = jobs.ANALYZE_EVENT


class NoDelegate(Exception):
    """Nobody in this organization has lent the automation their authority.

    Not an error in the data. An organization that has enabled no capability has delegated nothing,
    which is the correct resting state of a deny-by-default policy (ADR-0047).
    """


def build_provider(settings: Settings) -> LLMProvider:
    """The model provider this deployment runs with (ADR-0049).

    Assembly, and deliberately not inside `app.agent`: a provider adapter knows nothing about
    configuration, and choosing between adapters is a deployment decision.

    The default is `fake`, so the real adapter is never on a default path — an environment that has
    not said which provider it wants does not quietly acquire one. An unknown name fails at startup
    rather than at the first analysis, and so does a provider named without a credential: both are
    deployment mistakes and should look like one before traffic arrives.
    """
    name = settings.llm_provider.strip().lower()
    if name in ("", "fake"):
        return FakeProvider()
    if name == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if name == "anthropic":
        return AnthropicProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise ValueError(f"unknown model provider: {settings.llm_provider!r}")


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


class RuntimeServices:
    """What the runtime is allowed to reach. Holds the session; exposes no way to it.

    Every method is a published service call carrying the runtime's own `Actor`, so the AI origin
    travels with the write rather than being asserted separately (ADR-0043).
    """

    def __init__(
        self,
        session: Session,
        principal: Principal,
        store: ObjectStore,
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


@dataclasses.dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """What one run produced, for a caller that has to report it."""

    interaction_id: uuid.UUID
    evidence_ids: tuple[uuid.UUID, ...]
    proposal_ids: tuple[uuid.UUID, ...]
    low_confidence: int


def analyse(
    session: Session,
    *,
    principal: Principal,
    routed_to_person_id: uuid.UUID | None,
    store: ObjectStore,
    provider: LLMProvider,
    event_id: uuid.UUID,
    agent: str = "extractor",
) -> AnalysisOutcome:
    """Read one Event and submit what the model found. Level 1: nothing is executed.

    The authorization happens **here**, not in the caller. BR-AI-03 — an agent's authority is the
    intersection of its own and the delegating human's — has to hold whichever way the run was
    started, and a check that each caller is trusted to perform is a check that the next caller
    forgets. CP23 found exactly that: the analyse endpoint's refusal of a read-only role turned out
    to be incidental rather than checked.
    """
    identity_of = AGENTS.get(agent)
    if identity_of is None:
        raise EntityNotFound("agent", uuid.UUID(int=0))

    authorize(
        principal,
        Action.CREATE,
        ResourceRef(
            type=ResourceType.PROPOSAL,
            org_id=principal.org_id,
            id=None,
            relations=frozenset(),
        ),
    )

    # The intersection: this agent's capabilities, the delegate's own authority, and the
    # organization's policy read from the database. An organization that has decided nothing
    # denies everything (ADR-0047).
    agent_principal = AgentPrincipal(
        identity=identity_of,
        delegated=principal,
        policy=intelligence.load_capability_policy(session, org_id=principal.org_id),
    )
    try:
        result = AgentRuntime(provider).analyze_event(
            RuntimeServices(
                session, principal, store, agent_principal, DEFAULT_CONFIDENCE_POLICY
            ),
            agent_principal,
            event_id,
            routed_to_person_id=routed_to_person_id,
        )
    except ProviderError as error:
        # Translated rather than allowed to escape, so the interaction the runtime already marked
        # `failed` is committed with the caller's transaction. Letting it raise would roll that
        # back and lose the record of a run that genuinely happened — a provider outage would be
        # invisible rather than merely unsuccessful (ADR-0049).
        raise UpstreamProviderError(
            f"{type(error).__name__}: {error}", retryable=error.retryable
        ) from error

    return AnalysisOutcome(
        interaction_id=result.interaction_id,
        evidence_ids=tuple(result.evidence_ids),
        proposal_ids=tuple(result.proposal_ids),
        low_confidence=result.low_confidence,
    )


def delegate_for(session: Session, *, org_id: uuid.UUID) -> uuid.UUID:
    """The person whose authority an automatic analysis runs under (ADR-0069).

    Whoever enabled a capability. Turning one on is a deliberate administrative act, recorded with
    the decider's name, and it is the closest thing this system has to somebody saying "the AI may
    act for me" — so nothing new has to be invented to answer a question the architecture answers
    everywhere else.

    The earliest decision wins when several people have enabled several capabilities, so the answer
    is stable: an analysis does not change whose authority it runs under because somebody enabled a
    second capability this morning.
    """
    decided = [
        row
        for row in intelligence.capability_policy_rows(session, org_id=org_id)
        if row.mode != "off" and row.decided_by_person_id is not None
    ]
    if not decided:
        raise NoDelegate(
            "no capability is enabled in this organization, so nobody has delegated anything"
        )
    return uuid.UUID(str(min(decided, key=lambda row: row.created_at).decided_by_person_id))


def analyse_in_background(
    session: Session, org_id: uuid.UUID, payload: dict[str, Any]
) -> str:
    """The queued form: same analysis, nobody waiting for it.

    Every reason to stop is an *outcome* rather than an exception, because a job that raises is a
    job that retries, and none of these improve by being tried again: an organization that has
    enabled nothing will still have enabled nothing in five minutes.
    """
    event_id = uuid.UUID(str(payload["event_id"]))
    try:
        person_id = delegate_for(session, org_id=org_id)
    except NoDelegate:
        # The ordinary resting state of a deny-by-default policy, and the cost guard: no enabled
        # capability means every intent would be refused, so the model is never called at all.
        return "skipped: no capability is enabled"

    try:
        principal = resolve_principal_for(session, org_id=org_id, person_id=person_id)
    except Exception as error:  # noqa: BLE001 - any failure to resolve is the same outcome
        # The person who delegated has lost their roles, or left. Refusing loudly is the point:
        # automation must not outlive the authority it runs on.
        return f"skipped: the delegate no longer holds a role ({type(error).__name__})"

    outcome = analyse(
        session,
        principal=principal,
        # Routed to the delegate: the person who said the AI may act for them is the person who
        # answers for what it proposes.
        routed_to_person_id=person_id,
        store=_default_store(),
        provider=build_provider(get_settings()),
        event_id=event_id,
    )
    return (
        f"interaction {outcome.interaction_id}: "
        f"{len(outcome.proposal_ids)} proposal(s), {len(outcome.evidence_ids)} evidence"
    )


def _default_store() -> ObjectStore:
    """Built per job rather than held, so a worker that never analyses never opens a client."""
    from app.platform.http.deps import default_object_store

    return default_object_store()
