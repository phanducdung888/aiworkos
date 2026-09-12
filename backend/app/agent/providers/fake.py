"""A deterministic provider for tests and local development.

Not a mock. It performs a real, if crude, extraction: the spans it returns genuinely index into the
text it found them in. That matters because BR-E-05 checks the excerpt against the Event — a mock
returning canned offsets would make the verbatim-excerpt rule untestable, which is the rule most
worth testing on this path.

Deterministic in the strict sense: same input, same output, every run, no network and no
credentials. The whole agent path is exercisable in CI without a provider account, which is what
stops these tests from being skipped in exactly the environment that should run them.

Its confidence is `HEURISTIC` and says so (ADR-0050). A pattern match is an honest guess about
whether a sentence is a promise; it is not a probability, and labelling it one would put an
uncalibrated number into the same field a real model's output goes into.
"""

from __future__ import annotations

import dataclasses
import re

from app.agent.providers.confidence import (
    UNKNOWN_CONFIDENCE,
    ConfidenceAssessment,
    ConfidenceNormalizer,
    ConfidenceSource,
)
from app.agent.providers.errors import (
    ProviderError,
    ProviderInvalidResponse,
    ProviderTimeout,
)
from app.agent.providers.port import (
    CompletionRequest,
    CompletionResult,
    ExtractedSpan,
    FinishReason,
    ModelIdentity,
)

#: Phrases that introduce a promise in the kind of message this system ingests. Crude on purpose —
#: this stands in for a model, and making it clever would hide how much of the pipeline's behaviour
#: depends on the model rather than on the plumbing.
_COMMITMENT = re.compile(r"\b(?:I|we)\s+(?:will|'ll|shall)\s+[^.!?\n]+", re.IGNORECASE)
_ACTION = re.compile(r"\b(?:please|can you|could you)\s+[^.!?\n]+", re.IGNORECASE)


@dataclasses.dataclass(frozen=True, slots=True)
class FakeScenario:
    """What this provider should do when asked.

    Failures are configured rather than triggered by magic input, so a test that wants a timeout
    says so instead of discovering that some sentence happens to provoke one.
    """

    #: Score reported for every span found, or None to report nothing at all.
    confidence_value: int | None = 85
    confidence_source: ConfidenceSource = ConfidenceSource.HEURISTIC
    #: Raised instead of answering. No sleeping: a test for timeout behaviour should not take as
    #: long as a timeout.
    raises: ProviderError | None = None
    #: Answer with something the contract cannot accept — a span pointing outside the text. Used
    #: to prove a malformed answer is a contract violation rather than a low confidence.
    malformed: bool = False
    finish_reason: FinishReason = FinishReason.COMPLETE
    #: Whether the provider can tell us which model version replied.
    resolve_version: bool = True


class FakeProvider:
    """`LLMProvider` with no model behind it."""

    name = "fake"

    def __init__(
        self,
        scenario: FakeScenario | None = None,
        *,
        model: str = "deterministic",
        version: str = "v1",
    ) -> None:
        self._scenario = scenario or FakeScenario()
        self._model = model
        self._version = version
        self._normalizer = ConfidenceNormalizer()

    def complete(self, request: CompletionRequest) -> CompletionResult:
        scenario = self._scenario
        if scenario.raises is not None:
            raise scenario.raises
        if scenario.malformed:
            # A span outside the text. The runtime must treat this as a broken contract, not as a
            # model that was unsure (ADR-0049).
            raise ProviderInvalidResponse(
                "the provider returned a span outside the supplied text"
            )

        assessment = self._assess()
        spans: list[ExtractedSpan] = []
        for pattern, kind in ((_COMMITMENT, "commitment"), (_ACTION, "work")):
            for match in pattern.finditer(request.text):
                if len(spans) >= request.max_spans:
                    break
                spans.append(
                    ExtractedSpan(
                        kind=kind,
                        summary=match.group(0).strip(),
                        char_start=match.start(),
                        # The span is the matched text exactly, so the excerpt built from it is
                        # verbatim by construction rather than by the caller trimming carefully.
                        char_end=match.start() + len(match.group(0).rstrip()),
                        confidence=assessment,
                    )
                )

        return CompletionResult(
            spans=tuple(spans),
            model=ModelIdentity(
                provider=self.name,
                name=self._model,
                version=self._version if scenario.resolve_version else self._model,
                resolved=scenario.resolve_version,
            ),
            finish_reason=scenario.finish_reason,
            token_usage={"input": len(request.text.split()), "output": len(spans) * 8},
            provider_request_id="fake-request",
        )

    def _assess(self) -> ConfidenceAssessment:
        if self._scenario.confidence_value is None:
            return UNKNOWN_CONFIDENCE
        return self._normalizer.from_value(
            self._scenario.confidence_value, source=self._scenario.confidence_source
        )


#: Ready-made scenarios, named so a test reads as what it is testing.
HIGH_CONFIDENCE = FakeScenario(confidence_value=90)
MEDIUM_CONFIDENCE = FakeScenario(confidence_value=70)
LOW_CONFIDENCE = FakeScenario(confidence_value=30)
UNKNOWN_CONFIDENCE_SCENARIO = FakeScenario(confidence_value=None)
MALFORMED = FakeScenario(malformed=True)
TIMES_OUT = FakeScenario(raises=ProviderTimeout("the provider did not answer in time"))
UNRESOLVED_VERSION = FakeScenario(resolve_version=False)
