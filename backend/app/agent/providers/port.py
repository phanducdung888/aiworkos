"""The LLM provider port (ADR-0049).

Provider-neutral by construction: nothing here names a vendor, and no module under
`app.agent.providers` may import `app.contexts` or `app.platform` at all (ADR-0045). A provider
talks to a model and returns structured output. It has no business authority — it cannot read the
database, call a service, reach the Tool Gateway, enqueue anything or decide an authorization, and
those are structural facts rather than instructions.

Two parts of the contract are worth reading before the types.

**A provider says what actually answered.** `model` and `model_version` describe the model that
*replied*. Routing means the reply can come from something other than what was asked for, and
recording the request would make `ai_interaction.model_version` a field nobody can trust for
BR-AI-32's promotion metrics. A provider that cannot resolve a version echoes the canonical
identifier it was given and marks it as unresolved — it never invents one.

**A malformed answer is a broken contract, not a low confidence.** `ProviderInvalidResponse` is
raised and no Proposal is produced. The model being unsure and the integration being broken are
different events, and collapsing them makes an outage look like a quiet day.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Protocol

from app.agent.providers.confidence import ConfidenceAssessment


class FinishReason(enum.StrEnum):
    """Why the model stopped.

    `LENGTH` matters to the caller: a truncated answer may have been about to say something, so it
    is not the same as a model that finished and found nothing.
    """

    COMPLETE = "complete"
    LENGTH = "length"
    REFUSED = "refused"
    #: The provider reported something this contract does not model. Recorded rather than mapped
    #: to the nearest neighbour, because guessing what a vendor meant is how a contract rots.
    OTHER = "other"


@dataclasses.dataclass(frozen=True, slots=True)
class ExtractedSpan:
    """A claim the model made, with the span of source text it came from.

    `char_start`/`char_end` are what BR-E-05 checks against: the excerpt must be what the Event
    actually says at that locator. A provider that returns a paraphrase produces Evidence the
    service refuses, which is the correct outcome and is why the span is required rather than
    optional.
    """

    kind: str
    summary: str
    char_start: int
    char_end: int
    confidence: ConfidenceAssessment
    #: Structured details the orchestration understands — a due date, a named handle. Never free
    #: text that would be persisted unlabelled (BR-AI-12).
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Which model answered.

    `resolved` is False when the provider could not tell us and `name` is the canonical identifier
    we asked for. That is an honest "we do not know precisely", and it is deliberately visible
    rather than hidden behind a plausible version string.
    """

    provider: str
    name: str
    version: str
    resolved: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class CompletionRequest:
    """What the orchestration asks of a model.

    `text` is the Event body. It is data, never instruction (BR-AI-10): anything inside it that
    looks like a command is content the provider must not act on, and `instruction` is the only
    source of task definition.
    """

    #: The pinned system prompt. Never "latest" — a run that cannot say which prompt produced it is
    #: not reproducible, and `ai_interaction` refuses an unpinned version outright.
    prompt_id: str
    prompt_version: str
    instruction: str
    text: str
    #: The model the caller wants. What actually answers is reported back and may differ.
    model: str
    max_spans: int = 25
    #: An opaque token a provider may use for its own caching across calls (ADR-0049). It is not
    #: business state: the runtime owns the conversation context, and nothing downstream reads a
    #: provider's memory as truth. Unused by every adapter today, and present so that a provider
    #: needing it does not force the port to change.
    conversation_id: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class CompletionResult:
    spans: tuple[ExtractedSpan, ...]
    model: ModelIdentity
    finish_reason: FinishReason = FinishReason.COMPLETE
    #: Counts only. No prompt body, no raw response and no credential — none of that is persisted
    #: anywhere, and an audit record must not become a second copy of the conversation.
    token_usage: dict[str, int] = dataclasses.field(default_factory=dict)
    #: The provider's own request identifier, when it gives one. Useful for a support ticket and
    #: meaningless to the domain, which is why it is a string and nothing reads it.
    provider_request_id: str | None = None


class LLMProvider(Protocol):
    """What the agent runtime is allowed to ask of a model.

    One call. No streaming, no tool-calling protocol and no conversation state, because the MVP
    capability is a single analysis pass over one Event — and a port that offered more would invite
    a capability nobody decided to add.
    """

    @property
    def name(self) -> str:
        """Provider identifier recorded on the interaction, e.g. `fake`, `anthropic`."""
        ...

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Ask the model. Raises a `ProviderError` subclass, never a transport exception."""
        ...
