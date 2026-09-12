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
from app.agent.providers.port import CompletionRequest, ExtractedSpan, LLMProvider
from app.platform.actor import Actor, ActorType
from app.platform.authz.agent import (
    AgentAuthorityError,
    AgentCapability,
    AgentPrincipal,
)
from app.platform.errors import EntityNotFound

#: BR-AI-09. Below this, an extraction is recorded as a low-confidence observation and produces no
#: Proposal. Guessing quietly is worse than silence — a wrong Proposal costs somebody a review and
#: teaches them to stop reading them carefully.
MIN_CONFIDENCE = 60

#: BR-AI-04. A single interaction may raise at most this many Proposals. A run that wants more has
#: misunderstood something, and the cost of finding out is a review queue nobody can face.
MAX_PROPOSALS = 25

#: Which extracted span kind becomes which tool call. The map is the whole vocabulary an agent has:
#: a span kind with no entry produces no Proposal, however confident the model was about it.
_SPAN_TOOLS: dict[str, tuple[str, str]] = {
    "work": ("create_work", "work"),
    "commitment": ("create_commitment", "commitment"),
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

    def create_evidence(
        self, actor: Actor, command: signal.CreateEvidence
    ) -> signal.Evidence: ...

    def raise_proposal(
        self, actor: Actor, command: intelligence.RaiseProposal
    ) -> intelligence.Proposal: ...


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


class AgentRuntime:
    """Provider-independent. Swapping the model changes the constructor argument, nothing else."""

    name = "in-process"

    def __init__(self, provider: LLMProvider, *, prompt_version: str = "2026-09-12") -> None:
        self._provider = provider
        self._prompt_version = prompt_version

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
                    # The body is data. Any instruction inside it is ignored (BR-AI-10).
                    text=event.body_text or "",
                    max_spans=MAX_PROPOSALS,
                )
            )
            evidence_ids, proposal_ids, low, duplicates = self._propose_from(
                services,
                principal,
                actor,
                event=event,
                spans=result.spans,
                routed_to_person_id=routed_to_person_id or principal.person_id,
            )
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
            model=result.model,
            model_version=result.model_version,
            token_usage=result.token_usage,
            output_summary={
                "evidence": len(evidence_ids),
                "proposals": len(proposal_ids),
                "low_confidence": low,
                # BR-AI-05's answer, kept as a number: how often the agent looked and found the
                # work already there. A rising count is the signal that something upstream is
                # re-delivering, not that the agent is being cautious.
                "duplicates_skipped": duplicates,
            },
        )
        return AnalysisResult(
            interaction_id=interaction_id,
            evidence_ids=evidence_ids,
            proposal_ids=proposal_ids,
            low_confidence=low,
            duplicates_skipped=duplicates,
        )

    def _propose_from(
        self,
        services: RuntimeServices,
        principal: AgentPrincipal,
        actor: Actor,
        *,
        event: signal.Event,
        spans: tuple[ExtractedSpan, ...],
        routed_to_person_id: uuid.UUID,
    ) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...], int, int]:
        evidence_ids: list[uuid.UUID] = []
        proposal_ids: list[uuid.UUID] = []
        low_confidence = 0
        duplicates = 0
        sequence = 0

        for span in spans:
            if len(proposal_ids) >= MAX_PROPOSALS:
                break
            if span.confidence < MIN_CONFIDENCE:
                low_confidence += 1
                continue

            mapping = _SPAN_TOOLS.get(span.kind)
            if mapping is None:
                low_confidence += 1
                continue
            tool_name, target_type = mapping

            sequence += 1

            # BR-AI-05. Look before proposing. The result is recorded as its own tool call, so
            # "did this interaction search before it proposed" is answerable from the audit trail
            # rather than from trusting that the code did.
            similar = services.find_similar_work(span.summary)
            services.record_tool_call(
                ai_interaction_id=actor.ai_interaction_id,
                sequence=sequence,
                tool_name="find_similar_work",
                tool_version="v1",
                arguments_redacted={"keys": ["title"]},
                authorization_result="allowed",
                outcome="succeeded",
            )
            sequence += 1
            if similar:
                # Something like this already exists. Not proposing is the correct outcome and a
                # normal one — an agent that reads ten messages about one deadline must not
                # propose ten Work items, because somebody then rejects nine and learns to stop
                # reading proposals carefully.
                services.record_tool_call(
                    ai_interaction_id=actor.ai_interaction_id,
                    sequence=sequence,
                    tool_name=tool_name,
                    tool_version="v1",
                    arguments_redacted={
                        "keys": sorted(span.attributes),
                        "similar_to": [str(row.id) for row in similar[:3]],
                    },
                    authorization_result="allowed",
                    outcome="refused",
                    error="similar work already exists (BR-AI-05)",
                )
                duplicates += 1
                continue

            # BR-AI-03's intersection, checked before the call and recorded either way. A denial is
            # the most interesting row in `tool_call`, so it is written before anything else.
            if not principal.may_use(tool_name):
                services.record_tool_call(
                    ai_interaction_id=actor.ai_interaction_id,
                    sequence=sequence,
                    tool_name=tool_name,
                    tool_version="v1",
                    arguments_redacted={"keys": sorted(span.attributes)},
                    authorization_result="denied",
                    outcome="refused",
                    error="outside the agent's effective tool set (BR-AI-03)",
                )
                continue

            evidence = services.create_evidence(
                actor,
                signal.CreateEvidence(
                    event_id=event.id,
                    target_type=target_type,
                    # A `create` Proposal has no target yet, so the citation points at the entity
                    # the Proposal would produce. It is resolved when the Proposal executes.
                    target_id=uuid.uuid4(),
                    assertion="creates",
                    locator=signal.TextLocator(
                        char_start=span.char_start, char_end=span.char_end
                    ),
                    # Verbatim, sliced from the Event itself rather than from the model's summary:
                    # BR-E-05 checks it against the source and a paraphrase would be refused.
                    excerpt=(event.body_text or "")[span.char_start : span.char_end],
                    confidence=span.confidence,
                ),
            )
            evidence_ids.append(evidence.id)

            proposal = services.raise_proposal(
                actor,
                intelligence.RaiseProposal(
                    kind=intelligence.ProposalKind.CREATE,
                    target_type=target_type,
                    summary=span.summary[:200],
                    reason=(
                        "Extracted from a captured message. The owner, date and project are left "
                        "empty unless the text stated them (BR-AI-17)."
                    ),
                    tool=tool_name,
                    tool_version="v1",
                    arguments=self._arguments_for(
                        span, target_type, principal, evidence_id=evidence.id
                    ),
                    routed_to_person_id=routed_to_person_id,
                    confidence=span.confidence,
                    source_event_id=event.id,
                    evidence_ids=(evidence.id,),
                ),
            )
            proposal_ids.append(proposal.id)
            services.record_tool_call(
                ai_interaction_id=actor.ai_interaction_id,
                sequence=sequence,
                tool_name=tool_name,
                tool_version="v1",
                arguments_redacted={"keys": sorted(span.attributes)},
                authorization_result="allowed",
                # `not_attempted` is exact: the Proposal was raised, the tool has not run and will
                # not until somebody approves it. Recording it as succeeded would say a mutation
                # happened.
                outcome="not_attempted",
                target_entity_type=target_type,
            )

        return tuple(evidence_ids), tuple(proposal_ids), low_confidence, duplicates

    def _arguments_for(
        self,
        span: ExtractedSpan,
        target_type: str,
        principal: AgentPrincipal,
        *,
        evidence_id: uuid.UUID,
    ) -> dict[str, Any]:
        """BR-AI-17: preserve uncertainty rather than resolve it.

        Nothing is invented to make a Proposal look complete. No owner is guessed (BR-AI-34), no
        project is inferred (BR-AI-35), and a date the text did not state is absent rather than
        estimated. A reviewer seeing an empty field learns something true.
        """
        if target_type == "commitment":
            return {
                "statement": span.summary,
                # The delegating person is the committer only when the model resolved nobody, and
                # it is stated as an attribute rather than guessed here.
                "committed_by_person_id": str(
                    span.attributes.get("committed_by_person_id", principal.person_id)
                ),
                # BR-C-03. The citation travels into the action, so the Commitment that eventually
                # executes can show where the promise was made. Carrying it on the Proposal alone
                # would not be enough — the tool builds the Commitment from these arguments, and a
                # promise the system asserts with no record of where it was said is the thing the
                # rule prevents.
                "evidence_ids": [str(evidence_id)],
            }
        return {"title": span.summary}
