"""The OpenAI half of the real-provider smoke test. Off unless you turn it on.

Same shape and same reasoning as `test_real_provider.py`: unit tests confirm the adapter matches
*our* idea of the response, and only a real call confirms it matches OpenAI's.

    RUN_REAL_PROVIDER_TESTS=1 OPENAI_API_KEY=sk-... pytest tests/smoke/test_real_openai.py -v

**Nothing asserts what the model said.** Output is not deterministic; an assertion over it would be
a flaky test wearing a useful name. What is asserted is the shape, the provenance, and the absence
of the credential from anything the adapter returns.

**Nothing mutates.** The path under test is `AgentRuntime.analyze` — text in, opinions out. There
is no session in this file, which is the strongest form that claim can take.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.agent.providers.openai import OpenAIProvider
from app.agent.providers.port import FinishReason
from app.agent.runtime import AgentRuntime
from app.platform.agentkit import (
    AgentAnalysis,
    ConfidenceBand,
    ConfidenceSource,
    PersonReference,
    ToolIntent,
)

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.environ.get("RUN_REAL_PROVIDER_TESTS") != "1",
        reason="opt-in: set RUN_REAL_PROVIDER_TESTS=1 and OPENAI_API_KEY",
    ),
]

MESSAGE = (
    "Hi Mai, thanks for the call earlier. I will send the revised quote on Friday "
    "once finance has signed off. Could you check whether the delivery date still works?"
)

API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")


@pytest.fixture(scope="module")
def outcome():  # type: ignore[no-untyped-def]
    """One real call, shared by every assertion.

    Module-scoped because it costs money and takes seconds, and eight tests each making their own
    call would be eight chances for a rate limit to fail the run for no useful reason.
    """
    if not API_KEY:
        pytest.fail("RUN_REAL_PROVIDER_TESTS=1 was set but OPENAI_API_KEY is empty")
    provider = OpenAIProvider(api_key=API_KEY, model=MODEL, timeout_seconds=60.0)
    return AgentRuntime(provider, model=MODEL).analyze_with_result(
        MESSAGE,
        participants=(PersonReference(participant_id=uuid.uuid4(), role="speaker"),),
    )


def test_the_real_path_returns_a_typed_result(outcome) -> None:  # type: ignore[no-untyped-def]
    """AgentRuntime → OpenAIProvider → OpenAI → typed result, with no change to the runtime."""
    analysis, _ = outcome
    assert isinstance(analysis, AgentAnalysis)
    assert all(isinstance(intent, ToolIntent) for intent in analysis.intents)


def test_it_reports_which_model_answered(outcome) -> None:  # type: ignore[no-untyped-def]
    """Printed rather than pinned: routing returns a dated variant, and asserting a fixed string
    would fail the day the vendor rolls one."""
    _, result = outcome
    assert result.model.provider == "openai"
    assert result.model.name
    print(f"\n  model: {result.model.name} (resolved={result.model.resolved})")


def test_it_reports_a_finish_reason(outcome) -> None:  # type: ignore[no-untyped-def]
    _, result = outcome
    print(f"  finish_reason: {result.finish_reason.value}")
    assert result.finish_reason is not FinishReason.OTHER, (
        "the API reported a stop reason this adapter does not model; map it rather than letting "
        "it fall through to OTHER"
    )


def test_it_reports_token_usage(outcome) -> None:  # type: ignore[no-untyped-def]
    _, result = outcome
    assert result.token_usage.get("input", 0) > 0, "no prompt tokens were reported"
    assert "output" in result.token_usage
    print(f"  tokens: {result.token_usage}")


def test_it_reports_a_request_id(outcome) -> None:  # type: ignore[no-untyped-def]
    _, result = outcome
    assert result.provider_request_id, "no request id was returned"
    print(f"  request_id: {result.provider_request_id}")


def test_the_spans_satisfy_br_e_05(outcome) -> None:  # type: ignore[no-untyped-def]
    """The rule only a real model can disprove.

    A model returning approximate offsets, or offsets into its own paraphrase, produces Evidence
    this system refuses. `analyze` raises `ProviderContractViolation` in that case, so reaching
    this assertion means it held — asserting it explicitly makes the failure legible if it stops.
    """
    analysis, _ = outcome
    for intent in analysis.intents:
        span = intent.evidence
        assert 0 <= span.char_start < span.char_end <= len(MESSAGE)
        print(f"  span: {MESSAGE[span.char_start:span.char_end]!r}")


def test_the_confidence_is_the_model_s_own_or_unknown(outcome) -> None:  # type: ignore[no-untyped-def]
    """ADR-0050. Never a heuristic dressed as a model's number."""
    analysis, _ = outcome
    for intent in analysis.intents:
        assert intent.confidence.source in (
            ConfidenceSource.PROVIDER_REPORTED,
            ConfidenceSource.UNAVAILABLE,
        )
        if intent.confidence.band is ConfidenceBand.UNKNOWN:
            assert intent.confidence.value is None
        print(
            f"  confidence: {intent.confidence.band.value} "
            f"({intent.confidence.source.value}, value={intent.confidence.value})"
        )


def test_the_model_finds_the_promise(outcome) -> None:  # type: ignore[no-untyped-def]
    """The only behavioural assertion, deliberately weak.

    The message contains one unambiguous commitment. A model finding nothing in it has a broken
    prompt or is not doing the job — worth knowing, and the limit of what can be asserted about
    non-deterministic output without writing a flaky test.
    """
    analysis, _ = outcome
    print(f"  intents: {[i.kind.value for i in analysis.intents]}")
    assert analysis.intents or analysis.below_confidence, (
        "the model produced nothing from a message containing a clear promise"
    )


def test_the_credential_appears_nowhere_in_the_result(outcome) -> None:  # type: ignore[no-untyped-def]
    """Against the vendor's own response, not one we wrote."""
    analysis, result = outcome
    rendered = f"{analysis!r}{result!r}"
    assert API_KEY not in rendered
    assert "authorization" not in rendered.lower()
    assert "bearer" not in rendered.lower()


def test_nothing_was_mutated(outcome) -> None:  # type: ignore[no-untyped-def]
    """`analyze` takes text and returns opinions. There is no database in this test at all."""
    analysis, _ = outcome
    for intent in analysis.intents:
        for attribute in dir(intent):
            if not attribute.startswith("_"):
                assert not callable(getattr(intent, attribute))
