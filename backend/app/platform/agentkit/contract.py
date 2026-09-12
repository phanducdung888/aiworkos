"""What an agent is allowed to produce (ADR-0052).

A `ToolIntent` is a *request to consider* a tool call. It is not an execution, not a Proposal, and
not a decision — it is the agent saying "I think this should happen, here is the evidence, here is
how sure I am". Whether it may happen is answered by `IntentValidator` inside WorkOS, which the
agent layer cannot import and cannot reach.

That separation is the whole point of this module. Before it, `AgentRuntime` chose the tool, built
the arguments and raised the Proposal, so every control on that path lived inside the component the
controls existed to constrain. An agent that constructs its own Proposal is an agent you have to
trust; an agent that emits typed intents is an agent whose output you can refuse.

**Everything here is data.** No method on these types does anything: they carry a request across a
boundary. An external agent — a different runtime, a different vendor, something nobody has written
yet — enters at exactly this point with no additional trust, because there is nothing to trust.
"""

from __future__ import annotations

import dataclasses
import enum
import uuid
from typing import Any, Protocol

from app.platform.agentkit.confidence import ConfidenceAssessment


class IntentKind(enum.StrEnum):
    """What sort of change an intent asks for.

    Deliberately coarse and deliberately not the tool name: the kind is what a *reviewer* would
    call it, and the tool is an implementation the validator resolves. An agent naming a tool the
    registry does not hold is refused; an agent naming a kind is refused for a different and more
    legible reason.
    """

    CREATE_WORK = "create_work"
    CREATE_COMMITMENT = "create_commitment"
    ASSIGN_WORK = "assign_work"


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """Where in the source text the agent found its reason.

    Required, not optional. An intent without a span is an assertion with no citation, and BR-E-05
    would refuse the Evidence it produced anyway — requiring it here means the refusal names the
    agent rather than the Evidence service.
    """

    char_start: int
    char_end: int


@dataclasses.dataclass(frozen=True, slots=True)
class PersonReference:
    """A person the agent believes is involved, and how it came to believe that.

    `participant_id` is the `EventParticipant` row the agent is pointing at. It is not a
    `person_id`, and that is the safeguard: an agent cannot name an arbitrary Person, only one the
    Event already resolved. BR-AI-34 forbids inventing or guessing a Person, and the way to make
    that structural rather than aspirational is to remove the agent's ability to express one.
    """

    participant_id: uuid.UUID
    role: str


@dataclasses.dataclass(frozen=True, slots=True)
class ToolIntent:
    """One thing an agent thinks should happen.

    `arguments` holds only the fields a tool's allow-list permits an agent to supply — free text it
    extracted, a date the source stated. Identifiers are never in here: they go in `people`, where
    they are references into the Event rather than raw ids, and the validator resolves them.
    """

    kind: IntentKind
    #: What a reviewer reads. Taken verbatim from the source where possible.
    summary: str
    #: Why the agent thinks this, in its own words. Never persisted as business state (BR-AI-12).
    rationale: str
    evidence: EvidenceSpan
    confidence: ConfidenceAssessment
    arguments: dict[str, Any] = dataclasses.field(default_factory=dict)
    people: tuple[PersonReference, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AgentAnalysis:
    """Everything one agent run produced. Data, end to end.

    Carries what the agent decided *not* to do as well as what it did: spans below the confidence
    policy, and spans it could not express as an intent. Those counts are BR-AI-09's "the model saw
    something and we chose not to act", and dropping them would make a cautious run and an empty one
    indistinguishable.
    """

    intents: tuple[ToolIntent, ...] = ()
    below_confidence: int = 0
    unresolved_attribution: int = 0
    inexpressible: int = 0


class AgentContract(Protocol):
    """What WorkOS asks of an agent, and the only thing it accepts back.

    One method. An implementation may be this repository's `AgentRuntime`, a different runtime, or
    something running elsewhere entirely — the contract is the same and confers no
    authority, because an `AgentAnalysis` is a description of opinions.
    """

    @property
    def name(self) -> str:
        """Identifies the agent implementation in `ai_interaction.runtime`."""
        ...

    def analyze(
        self, text: str, *, participants: tuple[PersonReference, ...]
    ) -> AgentAnalysis:
        """Read text, return intents.

        `text` is untrusted content (BR-AI-10). Anything inside it that looks like an instruction is
        data, and an implementation that treats it otherwise is broken in a way this contract cannot
        prevent — which is why the validator downstream trusts none of the output.

        `participants` is the set of people the Event already resolved. It is passed in so an
        agent has something legitimate to point at, and so that pointing at anything
        else is impossible rather than merely refused.
        """
        ...
