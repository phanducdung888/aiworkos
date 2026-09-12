"""A deterministic provider for tests and local development.

Not a mock. It performs a real, if crude, extraction: it looks for commitment-shaped language and
returns spans that genuinely point at the text it found them in. That matters because the spans it
returns are checked against the Event by BR-E-05 — a mock returning canned offsets would make the
verbatim-excerpt rule untestable, which is the rule most worth testing.

Deterministic in the strict sense: the same input produces the same output, every run, with no
network and no credentials. The whole agent path is exercisable in CI without a provider account,
which is what stops the tests from being skipped in exactly the environment that should run them.
"""

from __future__ import annotations

import re

from app.agent.providers.port import (
    CompletionRequest,
    CompletionResult,
    Confidence,
    ExtractedSpan,
)

#: Phrases that introduce a promise in the kind of message this system ingests. Crude on purpose —
#: this is a stand-in for a model, and pretending otherwise by making it clever would hide how much
#: of the pipeline's behaviour depends on the model rather than on the plumbing.
_COMMITMENT = re.compile(
    r"\b(?:I|we)\s+(?:will|'ll|shall)\s+[^.!?\n]+", re.IGNORECASE
)
_ACTION = re.compile(
    r"\b(?:please|can you|could you)\s+[^.!?\n]+", re.IGNORECASE
)


class FakeProvider:
    """`LLMProvider` with no model behind it."""

    name = "fake"

    def __init__(self, *, model: str = "deterministic", version: str = "v1") -> None:
        self._model = model
        self._version = version

    def complete(self, request: CompletionRequest) -> CompletionResult:
        spans: list[ExtractedSpan] = []
        for pattern, kind, confidence in (
            (_COMMITMENT, "commitment", Confidence.HIGH),
            (_ACTION, "work", Confidence.MEDIUM),
        ):
            for match in pattern.finditer(request.text):
                if len(spans) >= request.max_spans:
                    break
                # The span is the matched text exactly, so the excerpt built from it is verbatim by
                # construction rather than by the caller trimming carefully.
                spans.append(
                    ExtractedSpan(
                        kind=kind,
                        summary=match.group(0).strip(),
                        char_start=match.start(),
                        char_end=match.start() + len(match.group(0).rstrip()),
                        confidence=int(confidence),
                    )
                )
        return CompletionResult(
            spans=tuple(spans),
            model=self._model,
            model_version=self._version,
            token_usage={"input": len(request.text.split()), "output": len(spans) * 8},
        )
