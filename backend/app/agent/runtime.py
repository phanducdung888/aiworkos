"""The agent runtime (ADR-0045).

What an agent does in this MVP, in full: read one Event, ask a provider what it contains, record a
citation for each claim, and raise a Proposal for a human to decide. That is Level 1 — observe,
interpret, propose (PQ-3). It does not execute anything, and there is no code path here that could.

Three constraints hold it in place and none of them is a convention:

*No database.* This module may not import `app.platform.db`, `sqlalchemy`, or any context internal
(ADR-0045, enforced by `test_the_agent_layer_cannot_reach_the_database`). What it holds is a
context exposing published services, not a session.

*No authority of its own.* Every write goes through a service that authorizes the **delegated
person**. An agent working for a `member` reaches what that member reaches, and BR-AI-03's
intersection narrows it further.

*No self-declared origin.* The actor it builds is `ActorType.AI` with the interaction id, which is
what makes BR-AI-02's evidence requirement unavoidable downstream (ADR-0043). Nothing the runtime
can say about itself changes what it is allowed to do.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any, Protocol

import app.contexts.intelligence.public as intelligence
import app.contexts.signal.public as signal
import app.contexts.work.public as work
from app.agent.providers.errors import (
    ProviderContractViolation,
    ProviderError,
    ProviderTimeout,
)
from app.agent.providers.port import (
    CompletionRequest,
    CompletionResult,
    ExtractedSpan,
    LLMProvider,
)
from app.platform.actor import Actor, ActorType
from app.platform.agentkit.confidence import ConfidenceBand, ConfidencePolicy
from app.platform.agentkit.contract import (
    AgentAnalysis,
    EvidenceSpan,
    IntentKind,
    PersonReference,
    ToolIntent,
)
from app.platform.authz.agent import (
    AgentAuthorityError,
    AgentCapability,
    AgentPrincipal,
)
from app.platform.errors import EntityNotFound

#: BR-AI-09's threshold, as a policy object rather than a number (ADR-0050).
#:
#: The runtime asks this instead of comparing integers, so "what counts as confident enough" is a
#: decision with a name. `UNKNOWN` is refused by construction: an extraction whose confidence could
#: not be assessed is the case where the system knows least about what it is doing, and guessing
#: quietly is worse than silence.
DEFAULT_CONFIDENCE_POLICY = ConfidencePolicy(minimum_band=ConfidenceBand.MEDIUM)

#: BR-AI-04. A single interaction may raise at most this many Proposals. A run that wants more has
#: misunderstood something, and the cost of finding out is a review queue nobody can face.
MAX_PROPOSALS = 25

#: The system prompt. The only source of task definition — anything inside an Event that looks
#: like an instruction is content, and BR-AI-10 says it is ignored.
EXTRACTION_INSTRUCTION = (
    "Identify commitments and requested actions in the supplied text. Quote spans exactly as they "
    "appear. Do not infer an owner, a date or a project that the text does not state."
)

#: Which extracted span kind becomes which intent.
#:
#: A *kind*, not a tool name. The runtime says what sort of thing it thinks it found; WorkOS
#: resolves that to a tool, decides whether the tool exists, whether this agent may use it and
#: whether the arguments are permitted (ADR-0052). An agent that could name a tool directly could
#: name one the registry gained for a different caller.
_SPAN_INTENTS: dict[str, IntentKind] = {
    "work": IntentKind.CREATE_WORK,
    "commitment": IntentKind.CREATE_COMMITMENT,
}


class RuntimeServices(Protocol):
    """What the runtime is handed, and the only things it can reach.

    Deliberately not a `Session`. The runtime cannot open a transaction, cannot name a table and
    cannot construct one of these itself — it receives one, already scoped, from the caller that
    owns the transaction boundary.
    """

    def record_interaction(self, **fields: Any) -> uuid.UUID: ...

    def finish_interaction(self, interaction_id: uuid.UUID, **fields: Any) -> None: ...

    def record_tool_call(self, **fields: Any) -> None: ...

    def read_event(self, event_id: uuid.UUID) -> signal.Event | None: ...

    def find_similar_work(self, title: str) -> list[work.SimilarWork]: ...

    def resolved_participants(
        self, event_id: uuid.UUID
    ) -> tuple[PersonReference, ...]: ...

    def submit_analysis(
        self,
        actor: Actor,
        event: signal.Event,
        analysis: AgentAnalysis,
        *,
        routed_to_person_id: uuid.UUID,
    ) -> SubmissionResult:
        """Hand the intents to WorkOS. It validates them and decides what becomes a Proposal.

        The runtime has no `create_evidence` and no `raise_proposal` any more. That is the
        boundary: an agent emits opinions, and the thing that turns an opinion into a reviewable
        change is code the agent cannot reach (ADR-0052).
        """
        ...


@dataclasses.dataclass(frozen=True, slots=True)
class SubmissionResult:
    """What WorkOS made of one analysis."""

    evidence_ids: tuple[uuid.UUID, ...] = ()
    proposal_ids: tuple[uuid.UUID, ...] = ()
    #: Intents the validator refused, with the rule each broke. Counted separately from
    #: low-confidence spans because a refusal means the agent asked for something it may not have,
    #: which is a different signal from the model being unsure.
    refused: int = 0
    #: Intents naming nobody the Event resolved (BR-AI-34).
    unresolved_attribution: int = 0
    duplicates_skipped: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class AnalysisResult:
    interaction_id: uuid.UUID
    evidence_ids: tuple[uuid.UUID, ...]
    proposal_ids: tuple[uuid.UUID, ...]
    #: Spans the model produced that were below threshold. Recorded on the interaction rather than
    #: discarded, because "the model saw something and we chose not to act" is the observation
    #: BR-AI-09 wants kept.
    low_confidence: int
    #: Spans that matched existing Work and produced no Proposal (BR-AI-05).
    duplicates_skipped: int = 0
    #: Intents WorkOS refused (ADR-0052). Distinct from low confidence: the agent asked for
    #: something it may not have, rather than being unsure about something it may.
    refused: int = 0
    #: Intents that named nobody the Event resolved (BR-AI-34).
    unresolved_attribution: int = 0


class AgentRuntime:
    """Provider-independent. Swapping the model changes the constructor argument, nothing else."""

    name = "in-process"

    def __init__(
        self,
        provider: LLMProvider,
        *,
        prompt_version: str = "2026-09-12",
        confidence_policy: ConfidencePolicy | None = None,
        model: str = "default",
    ) -> None:
        self._provider = provider
        self._prompt_version = prompt_version
        self._confidence = confidence_policy or DEFAULT_CONFIDENCE_POLICY
        self._model = model

    def analyze_event(
        self,
        services: RuntimeServices,
        principal: AgentPrincipal,
        event_id: uuid.UUID,
        *,
        routed_to_person_id: uuid.UUID | None = None,
    ) -> AnalysisResult:
        """Level 1, end to end. Nothing here executes; everything ends in a Proposal."""
        if AgentCapability.EXTRACT not in principal.identity.capabilities:
            raise AgentAuthorityError(
                f"{principal.identity.name} does not hold the extract capability"
            )

        event = services.read_event(event_id)
        if event is None:
            # Absent, not forbidden — the same answer reading it directly would give (BR-E-08). An
            # agent borrowing a person's authority sees exactly what that person sees, and telling
            # the caller "you may not analyse this" would confirm a restricted Event exists.
            raise EntityNotFound("event", event_id)

        # BR-E-11. Internal-origin Events are never extracted, or an AI-proposed Work item becomes
        # an Event and is re-extracted into another proposal, forever.
        if not signal.may_extract(
            signal.EventOrigin(event.origin), signal.Sensitivity(event.sensitivity)
        ):
            raise AgentAuthorityError(
                f"an event with origin={event.origin} and sensitivity={event.sensitivity} "
                "is not eligible for extraction (BR-E-11, BR-E-08)"
            )

        started = dt.datetime.now(dt.UTC)
        interaction_id = services.record_interaction(
            kind="extraction",
            trigger_type="event",
            trigger_ref=event.id,
            principal_person_id=principal.person_id,
            agent_identity=principal.identity.name,
            runtime=self.name,
            provider=self._provider.name,
            model="",
            model_version="",
            prompt_id="extract.commitments",
            prompt_version=self._prompt_version,
            tool_manifest_version=intelligence.REGISTRY_VERSION,
            input_refs={"event_ids": [str(event.id)]},
        )
        # The actor exists only now, and only with this id. Everything the run writes carries it,
        # which is what makes the provenance chain complete rather than best-effort (BR-AI-02).
        actor = Actor(
            type=ActorType.AI,
            person_id=principal.person_id,
            service_account=principal.identity.name,
            ai_interaction_id=interaction_id,
        )

        try:
            result = self._provider.complete(
                CompletionRequest(
                    prompt_id="extract.commitments",
                    prompt_version=self._prompt_version,
                    instruction=EXTRACTION_INSTRUCTION,
                    # The body is data. Any instruction inside it is ignored (BR-AI-10).
                    text=event.body_text or "",
                    model=self._model,
                    max_spans=MAX_PROPOSALS,
                )
            )
            self._assert_spans_fit(event, result)
            analysis = self._intents_from(
                event=event,
                spans=result.spans,
                participants=services.resolved_participants(event.id),
            )
            submission = services.submit_analysis(
                actor,
                event,
                analysis,
                routed_to_person_id=routed_to_person_id or principal.person_id,
            )
            evidence_ids = submission.evidence_ids
            proposal_ids = submission.proposal_ids
            low = analysis.below_confidence
            duplicates = submission.duplicates_skipped
        except ProviderError as error:
            # The interaction is marked before re-raising so that a caller which *does* commit —
            # a worker, a future batch runner — keeps the record. In the HTTP path it does not
            # survive: `scoped_session` rolls back on any exception, deliberately, because a
            # service that kept an audit entry for a mutation that then failed would be lying.
            #
            # So a provider outage is visible as a 502/504 and in logs, not as a row. That is a
            # real gap and it is recorded as one rather than papered over with a second
            # transaction bolted on here — the fix belongs with whatever writes operational
            # telemetry, not with the orchestration.
            services.finish_interaction(
                interaction_id,
                status="timed_out" if isinstance(error, ProviderTimeout) else "failed",
                finished_at=dt.datetime.now(dt.UTC),
                error=f"{type(error).__name__}: {error}"[:2000],
            )
            raise
        except Exception as error:
            services.finish_interaction(
                interaction_id,
                status="failed",
                finished_at=dt.datetime.now(dt.UTC),
                error=str(error)[:2000],
            )
            raise

        finished = dt.datetime.now(dt.UTC)
        services.finish_interaction(
            interaction_id,
            status="succeeded",
            finished_at=finished,
            latency_ms=int((finished - started).total_seconds() * 1000),
            # What actually answered, not what was asked for (ADR-0049).
            model=result.model.name,
            model_version=result.model.version,
            token_usage=result.token_usage,
            output_summary={
                "evidence": len(evidence_ids),
                "proposals": len(proposal_ids),
                "low_confidence": low,
                # ADR-0050. The source travels with the counts, so an evaluation can separate a
                # model's own claim from an adapter's heuristic instead of averaging them.
                "confidence_source": (
                    result.spans[0].confidence.source.value if result.spans else None
                ),
                "confidence_band": (
                    result.spans[0].confidence.band.value if result.spans else None
                ),
                "finish_reason": result.finish_reason.value,
                "model_version_resolved": result.model.resolved,
                # BR-AI-05's answer, kept as a number: how often the agent looked and found the
                # work already there. A rising count is the signal that something upstream is
                # re-delivering, not that the agent is being cautious.
                "duplicates_skipped": duplicates,
                "refused": submission.refused,
                "unresolved_attribution": submission.unresolved_attribution,
                "inexpressible": analysis.inexpressible,
            },
        )
        return AnalysisResult(
            interaction_id=interaction_id,
            evidence_ids=evidence_ids,
            proposal_ids=proposal_ids,
            low_confidence=low,
            duplicates_skipped=duplicates,
            refused=submission.refused,
            unresolved_attribution=submission.unresolved_attribution,
        )

    def _intents_from(
        self,
        *,
        event: signal.Event,
        spans: tuple[ExtractedSpan, ...],
        participants: tuple[PersonReference, ...],
    ) -> AgentAnalysis:
        """Turn spans into intents. Decides nothing; asks for everything.

        The only judgement made here is the confidence policy, and it is made by asking
        `ConfidencePolicy` rather than by comparing a number — because "is this worth raising at
        all" is the agent's own question, and everything after it belongs to WorkOS.
        """
        intents: list[ToolIntent] = []
        below = 0
        inexpressible = 0

        for span in spans:
            if len(intents) >= MAX_PROPOSALS:
                break
            if not self._confidence.admits(span.confidence):
                # BR-AI-09. Counted rather than discarded: "the model saw something and we chose
                # not to act" is the observation the rule wants kept, and `UNKNOWN` lands here too.
                below += 1
                continue

            kind = _SPAN_INTENTS.get(span.kind)
            if kind is None:
                inexpressible += 1
                continue

            people = self._people_for(kind, participants)
            if kind is IntentKind.CREATE_COMMITMENT and not people:
                # Nobody the Event resolved said this, so there is no defensible committer.
                #
                # Degraded to Work rather than dropped or attributed. The observation is real —
                # something needs doing — and Work needs no owner (BR-W-07, BR-W-15). What is not
                # evidenced is *who promised*, and BR-AI-34 forbids supplying that from a guess or,
                # as this runtime used to, from whoever happened to run the analysis.
                #
                # Preserving the useful half and dropping the unevidenced half is BR-AI-17 applied
                # to the shape of the proposal rather than to one of its fields.
                kind = IntentKind.CREATE_WORK

            intents.append(
                ToolIntent(
                    kind=kind,
                    summary=span.summary,
                    rationale=(
                        "Extracted from captured text. Owner, date and project are left empty "
                        "unless the source stated them (BR-AI-17)."
                    ),
                    evidence=EvidenceSpan(
                        char_start=span.char_start, char_end=span.char_end
                    ),
                    confidence=span.confidence,
                    arguments=self._arguments_for(kind, span),
                    # Only participants the Event already resolved. The agent cannot name anybody
                    # else, which is how BR-AI-34 becomes structural rather than aspirational.
                    people=people if kind is IntentKind.CREATE_COMMITMENT else (),
                )
            )

        return AgentAnalysis(
            intents=tuple(intents),
            below_confidence=below,
            inexpressible=inexpressible,
        )

    def _arguments_for(self, kind: IntentKind, span: ExtractedSpan) -> dict[str, Any]:
        """BR-AI-17: preserve uncertainty rather than resolve it.

        Nothing is invented to make an intent look complete, and no identifier appears here at all
        — the validator's allow-list refuses one, and there is nothing for the runtime to put in it
        that would not be a guess.
        """
        if kind is IntentKind.CREATE_COMMITMENT:
            return {"statement": span.summary}
        return {"title": span.summary}

    def _people_for(
        self, kind: IntentKind, participants: tuple[PersonReference, ...]
    ) -> tuple[PersonReference, ...]:
        """Who the intent names, chosen only from what the Event resolved.

        A commitment needs a committer, and the only defensible candidate is somebody the Event
        recorded as having spoken. When there is none, the intent carries nobody and the validator
        refuses it — which is the correct outcome, and is counted as an unresolved attribution
        rather than filled in with the person who ran the analysis.
        """
        if kind is not IntentKind.CREATE_COMMITMENT:
            return ()
        speakers = [p for p in participants if p.role in ("speaker", "organiser")]
        return (speakers[0],) if speakers else ()

    def _assert_spans_fit(self, event: signal.Event, result: CompletionResult) -> None:
        """Every span must index into the text we sent (ADR-0049).

        Checked here rather than trusted, and raised as a contract violation rather than silently
        dropped. A provider returning a span outside the body is broken, and BR-E-05 would refuse
        the resulting Evidence anyway — catching it at the boundary names the right culprit.
        """
        body = event.body_text or ""
        for span in result.spans:
            if not 0 <= span.char_start < span.char_end <= len(body):
                raise ProviderContractViolation(
                    f"span [{span.char_start}:{span.char_end}] does not fit the supplied text"
                )
