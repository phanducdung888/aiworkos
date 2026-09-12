"""The LLM provider port.

Provider-neutral by construction: nothing in this module names a vendor, and no module under
`app.agent.providers` may import `app.contexts` at all (ADR-0045). A provider adapter talks to a
model and returns structured output; it has no business knowing what an Event is.

The interface is deliberately narrow — one call, structured in, structured out. There is no
streaming, no tool-calling protocol and no conversation state, because the MVP capability is a
single analysis pass over one Event. A provider that needs more is a change to this port made
deliberately, not a capability that arrives because some SDK offered it.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Protocol


class Confidence(enum.IntEnum):
    """Coarse bands rather than a float.

    Extraction confidence drives BR-AI-09's threshold, and a model's self-reported probability is
    not calibrated enough to deserve two decimal places. Bands make the threshold a decision
    somebody made rather than a number somebody tuned.
    """

    LOW = 25
    MEDIUM = 60
    HIGH = 85


@dataclasses.dataclass(frozen=True, slots=True)
class ExtractedSpan:
    """A claim the model made, with the span of source text it came from.

    `char_start`/`char_end` are what BR-E-05 checks against: the excerpt must be what the Event
    actually says at that locator. A provider that returns a paraphrase produces Evidence that is
    refused, which is the correct outcome and is why the span is required rather than optional.
    """

    kind: str
    summary: str
    char_start: int
    char_end: int
    confidence: int
    #: Structured details the orchestration understands — a due date, a named handle. Never free
    #: text that would be persisted unlabelled (BR-AI-12).
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class CompletionRequest:
    """What the orchestration asks of a model.

    `text` is the Event body. It is data, never instruction (BR-AI-10): anything inside it that
    looks like a command is content the provider must not act on, and the system prompt is the only
    source of task definition.
    """

    prompt_id: str
    prompt_version: str
    text: str
    max_spans: int = 25


@dataclasses.dataclass(frozen=True, slots=True)
class CompletionResult:
    spans: tuple[ExtractedSpan, ...]
    model: str
    model_version: str
    #: Counts only. No prompt body, no raw response — those are not persisted anywhere (ADR-0043's
    #: neighbour concern: an audit record should not become a second copy of the conversation).
    token_usage: dict[str, int] = dataclasses.field(default_factory=dict)


class LLMProvider(Protocol):
    """What the agent runtime is allowed to ask of a model."""

    @property
    def name(self) -> str:
        """Provider identifier recorded on the interaction, e.g. `fake`, `anthropic`."""
        ...

    def complete(self, request: CompletionRequest) -> CompletionResult:
        ...
