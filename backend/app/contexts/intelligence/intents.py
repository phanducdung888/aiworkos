"""Turning an agent's intents into Proposals, or refusing them (ADR-0052).

This is the boundary an agent cannot cross. It lives in the Intelligence context, which
`app.agent` may not import, so the component being constrained has no way to reach the constraint.

Five independent refusals, and each exists because one of them alone would be insufficient:

1. **The tool is not registered.** ADR-0042's closed registry, applied to what the agent asked for
   rather than only to what eventually executes.
2. **Capability ∩ policy does not reach it.** ADR-0047's intersection.
3. **The action or resource is forbidden outright.** BR-AI-08 and BR-AI-23, through
   `assert_within_agent_authority` — which until this checkpoint existed and was never called by
   anything. That is the regression this module was written around.
4. **An argument is not permitted, or a Person reference is not a participant.** BR-AI-34: an agent
   may not invent, guess or nearest-match a Person, and the way to make that structural is to make
   an unresolvable attribution unexpressible.
5. **The confidence is below policy.** BR-AI-09, through the assessment the provider reported.

None of these trusts the agent. The intent is data produced by something that read untrusted text,
and it is treated as such the whole way through.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from app.contexts.intelligence import gateway
from app.platform.agentkit.confidence import ConfidencePolicy
from app.platform.agentkit.contract import AgentAnalysis, IntentKind, ToolIntent
from app.platform.authz import Action, ResourceType
from app.platform.authz.agent import (
    AgentAuthorityError,
    AgentPrincipal,
    assert_within_agent_authority,
)

#: What each intent kind resolves to, and what it touches.
#:
#: The agent names a *kind*; WorkOS decides the tool. An agent that could name a tool directly could
#: name one the registry gained for a different caller, and the capability check would be the only
#: thing between it and a call nobody meant it to make.
_RESOLUTION: dict[IntentKind, tuple[str, str, Action, ResourceType]] = {
    IntentKind.CREATE_WORK: ("create_work", "work", Action.CREATE, ResourceType.WORK),
    IntentKind.CREATE_COMMITMENT: (
        "create_commitment",
        "commitment",
        Action.CREATE,
        ResourceType.COMMITMENT,
    ),
    IntentKind.ASSIGN_WORK: (
        "assign_work",
        "work_assignment",
        Action.ASSIGN,
        ResourceType.WORK_ASSIGNMENT,
    ),
}

#: Which arguments an agent may supply, per kind.
#:
#: An allow-list rather than a denylist, and it holds no identifier of any kind. Everything an agent
#: can put here is text or a date it extracted; anything that names an entity goes through
#: `people`, where it is a reference into the Event rather than a value the agent chose.
_ALLOWED_ARGUMENTS: dict[IntentKind, frozenset[str]] = {
    IntentKind.CREATE_WORK: frozenset({"title", "description", "due_date"}),
    IntentKind.CREATE_COMMITMENT: frozenset({"statement", "due_date", "due_precision"}),
    IntentKind.ASSIGN_WORK: frozenset({"role"}),
}

#: Kinds that cannot be proposed without naming a person, and which person role satisfies that.
#:
#: A commitment with no identified committer is not a commitment somebody made — it is a sentence
#: the model read. BR-C-01 requires a committer and BR-AI-34 forbids guessing one, so the only
#: correct outcome when nobody resolves is no Proposal, counted as an unresolved attribution.
_REQUIRES_PERSON: dict[IntentKind, str] = {
    IntentKind.CREATE_COMMITMENT: "committed_by_person_id",
    IntentKind.ASSIGN_WORK: "person_id",
}


class IntentRefused(Exception):
    """An intent was not permitted. Carries the rule so the refusal is legible in `tool_call`."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedParticipant:
    """A participant of the source Event, as WorkOS resolved it — not as the agent described it."""

    participant_id: uuid.UUID
    person_id: uuid.UUID | None
    role: str


@dataclasses.dataclass(frozen=True, slots=True)
class ValidatedIntent:
    """An intent that survived every check, rendered as something the Proposal service can take."""

    kind: IntentKind
    tool: str
    target_type: str
    summary: str
    rationale: str
    arguments: dict[str, Any]
    confidence: int
    char_start: int
    char_end: int


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationOutcome:
    accepted: tuple[ValidatedIntent, ...] = ()
    #: Every refusal, with its rule. Recorded as `tool_call` rows with `denied`, because a refusal
    #: is the authority model working and is the row worth reading.
    refused: tuple[tuple[ToolIntent, str], ...] = ()


class IntentValidator:
    """The only path from an agent's opinion to a Proposal."""

    def __init__(
        self,
        principal: AgentPrincipal,
        *,
        participants: tuple[ResolvedParticipant, ...],
        confidence_policy: ConfidencePolicy,
        body_length: int,
    ) -> None:
        self._principal = principal
        self._participants = {p.participant_id: p for p in participants}
        self._confidence = confidence_policy
        self._body_length = body_length

    def validate(self, analysis: AgentAnalysis) -> ValidationOutcome:
        accepted: list[ValidatedIntent] = []
        refused: list[tuple[ToolIntent, str]] = []
        for intent in analysis.intents:
            try:
                accepted.append(self._one(intent))
            except IntentRefused as refusal:
                refused.append((intent, str(refusal)))
        return ValidationOutcome(accepted=tuple(accepted), refused=tuple(refused))

    # ------------------------------------------------------------------ one intent

    def _one(self, intent: ToolIntent) -> ValidatedIntent:
        resolution = _RESOLUTION.get(intent.kind)
        if resolution is None:
            raise IntentRefused("BR-AI-16", f"{intent.kind} is not a proposable kind")
        tool_name, target_type, action, resource_type = resolution

        # (1) The registry decides what exists, not the agent (ADR-0042).
        tool = gateway.REGISTRY.get((tool_name, "v1"))
        if tool is None:
            raise IntentRefused("BR-AI-16", f"no tool named {tool_name} is registered")

        # (2) Capability ∩ policy (ADR-0047). An empty policy denies everything.
        if not self._principal.may_use(tool_name):
            raise IntentRefused(
                "BR-AI-03", f"{tool_name} is outside this agent's effective tool set"
            )

        # (3) The absolute refusals, finally invoked. Not policy-configurable: a delegating admin
        # could change a role, and an agent borrowing their authority still cannot (BR-AI-08,
        # BR-AI-23).
        try:
            assert_within_agent_authority(action, resource_type)
        except AgentAuthorityError as error:
            raise IntentRefused("BR-AI-23", str(error)) from error

        # (4) Confidence, from the assessment the provider reported (BR-AI-09, ADR-0050).
        if not self._confidence.admits(intent.confidence):
            raise IntentRefused(
                "BR-AI-09",
                f"confidence {intent.confidence.band.value} is below the policy",
            )

        self._assert_span_fits(intent)
        arguments = self._arguments(intent)
        return ValidatedIntent(
            kind=intent.kind,
            tool=tool_name,
            target_type=target_type,
            summary=intent.summary.strip()[:200],
            rationale=intent.rationale.strip()[:1000],
            arguments=arguments,
            confidence=intent.confidence.value or 0,
            char_start=intent.evidence.char_start,
            char_end=intent.evidence.char_end,
        )

    def _assert_span_fits(self, intent: ToolIntent) -> None:
        """The citation must index into the text the agent was given.

        Checked here as well as at the provider boundary, because an intent may arrive from
        somewhere that never passed through a provider adapter — which is the whole point of having
        a contract rather than a function call.
        """
        span = intent.evidence
        if not 0 <= span.char_start < span.char_end <= self._body_length:
            raise IntentRefused(
                "BR-E-05", "the cited span does not fit the source text"
            )

    def _arguments(self, intent: ToolIntent) -> dict[str, Any]:
        """(5) Allow-list the fields, and resolve people through the Event.

        An agent cannot put an identifier in `arguments` at all: unknown keys are refused rather
        than dropped, because silently discarding a field means storing something other than what
        was asked for.
        """
        permitted = _ALLOWED_ARGUMENTS[intent.kind]
        unknown = intent.arguments.keys() - permitted
        if unknown:
            raise IntentRefused(
                "BR-AI-17",
                f"an agent may not supply {', '.join(sorted(unknown))} for {intent.kind.value}",
            )
        for key, value in intent.arguments.items():
            if isinstance(value, (dict, list, tuple)):
                raise IntentRefused("BR-AI-17", f"{key} must be a scalar")

        arguments: dict[str, Any] = dict(intent.arguments)
        required = _REQUIRES_PERSON.get(intent.kind)
        if required is not None:
            arguments[required] = str(self._resolve_person(intent))
        return arguments

    def _resolve_person(self, intent: ToolIntent) -> uuid.UUID:
        """BR-AI-34. The only Person an agent may name is one the Event already resolved.

        Not "a person that exists in this organization": existing is not evidence that *this*
        person made *this* promise. And not the delegating human either, which is what the runtime
        used to fall back to — attributing an extracted commitment to whoever ran the analysis is
        worse than guessing, because it is systematically wrong in a way that looks plausible.

        When nobody resolves, there is no Proposal. That is the correct outcome, and the caller
        records it as an unresolved attribution rather than as a failure.
        """
        if not intent.people:
            raise IntentRefused(
                "BR-AI-34",
                f"{intent.kind.value} names no person and one cannot be inferred",
            )
        reference = intent.people[0]
        participant = self._participants.get(reference.participant_id)
        if participant is None:
            raise IntentRefused(
                "BR-AI-34", "the referenced participant is not on the cited event"
            )
        if participant.person_id is None:
            raise IntentRefused(
                "BR-AI-34",
                "the participant is unresolved; the raw identifier is kept and no person is "
                "guessed",
            )
        return participant.person_id
