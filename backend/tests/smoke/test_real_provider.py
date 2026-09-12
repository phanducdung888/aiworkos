"""The one test that talks to a real model. Off unless you turn it on.

Everything else in this suite runs against `FakeProvider` — no network, no credential, no skipping
in the environment that should be running it. That is right for a test suite and it leaves one
thing unproven: whether the real API's response shape is what `AnthropicProvider` parses. Unit
tests drive a stubbed transport and can only confirm the adapter matches *our* idea of the
response.

So this exists, and it is deliberately awkward to run. It needs an environment variable and a
credential, and it is skipped otherwise, because a smoke test that runs accidentally is a smoke
test that bills somebody and fails a build for reasons nobody can reproduce.

    RUN_REAL_PROVIDER_TESTS=1 ANTHROPIC_API_KEY=sk-... pytest tests/smoke -v

**Nothing here asserts what the model said.** A model's output is not deterministic, and an
assertion over it would be a flaky test wearing a useful name. What is asserted is the *shape*:
that a typed result came back, that it says which model answered, that spans index into the text we
sent, and that the credential appears nowhere in what the adapter returns.

**Nothing here mutates anything.** There is no session, no Event and no Proposal — the path under
test is `AgentRuntime.analyze`, which takes text and returns opinions. Proving the model path works
does not require writing to a database, and a smoke test that did would be a smoke test nobody
dares run twice.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.agent.providers.anthropic import AnthropicProvider
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
        reason="opt-in: set RUN_REAL_PROVIDER_TESTS=1 and ANTHROPIC_API_KEY",
    ),
]

#: Deliberately ordinary. A message that reads like something a person would actually send, with
#: one clear promise in it — the point is to find out what the model does with realistic input,
#: not to construct something it cannot miss.
MESSAGE = (
    "Hi Mai, thanks for the call earlier. I will send the revised quote on Friday "
    "once finance has signed off. Could you check whether the delivery date still works?"
)

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


@pytest.fixture(scope="module")
def provider() -> AnthropicProvider:
    if not API_KEY:
        pytest.fail("RUN_REAL_PROVIDER_TESTS=1 was set but ANTHROPIC_API_KEY is empty")
    return AnthropicProvider(api_key=API_KEY, model=MODEL, timeout_seconds=60.0)


@pytest.fixture(scope="module")
def outcome(provider: AnthropicProvider):  # type: ignore[no-untyped-def]
    """One real call, shared by every assertion below.

    Module-scoped on purpose: this costs money and takes seconds, and eight tests that each made
    their own call would be eight chances for a rate limit to fail the run for no useful reason.
    """
    runtime = AgentRuntime(provider, model=MODEL)
    return runtime.analyze_with_result(
        MESSAGE,
        participants=(
            PersonReference(participant_id=uuid.uuid4(), role="speaker"),
        ),
    )


# --------------------------------------------------------------------------- the path works


def test_the_real_path_returns_a_typed_result(outcome) -> None:  # type: ignore[no-untyped-def]
    """AgentRuntime → AnthropicProvider → Anthropic → typed result.

    The whole point of the checkpoint, in one assertion: something came back and it is the type
    this system expects, not a dict somebody has to interpret.
    """
    analysis, result = outcome
    assert isinstance(analysis, AgentAnalysis)
    assert all(isinstance(intent, ToolIntent) for intent in analysis.intents)


def test_it_reports_which_model_answered(outcome) -> None:  # type: ignore[no-untyped-def]
    """ADR-0049. What replied, not what was asked for.

    Printed rather than asserted against a fixed string: model routing means the answer may be a
    dated variant of what we requested, and pinning it would fail the day the vendor rolls one.
    What matters is that the adapter reports *something* and is honest about whether it resolved.
    """
    _, result = outcome
    assert result.model.provider == "anthropic"
    assert result.model.name, "the adapter reported no model at all"
    print(f"\n  model: {result.model.name} (resolved={result.model.resolved})")


def test_it_reports_a_finish_reason(outcome) -> None:  # type: ignore[no-untyped-def]
    _, result = outcome
    assert isinstance(result.finish_reason, FinishReason)
    print(f"  finish_reason: {result.finish_reason.value}")
    assert result.finish_reason is not FinishReason.OTHER, (
        "the API reported a stop reason this adapter does not model; "
        "map it rather than letting it fall through to OTHER"
    )


def test_it_reports_token_usage(outcome) -> None:  # type: ignore[no-untyped-def]
    _, result = outcome
    assert result.token_usage.get("input", 0) > 0, "no input tokens were reported"
    assert "output" in result.token_usage
    print(f"  tokens: {result.token_usage}")


def test_it_reports_a_request_id(outcome) -> None:  # type: ignore[no-untyped-def]
    """Meaningless to the domain and exactly what a support ticket needs."""
    _, result = outcome
    assert result.provider_request_id, "no provider request id was returned"
    print(f"  request_id: {result.provider_request_id}")


# --------------------------------------------------------------------------- the output is usable


def test_the_spans_index_into_the_text_we_sent(outcome) -> None:  # type: ignore[no-untyped-def]
    """BR-E-05 depends on this and nothing else can substitute for checking it against a real model.

    A model that returns approximate offsets, or offsets into its own paraphrase, produces Evidence
    this system refuses. `analyze` already raises `ProviderContractViolation` in that case, so
    reaching this assertion means it held — but asserting it explicitly is what makes the failure
    legible if it ever stops.
    """
    analysis, _ = outcome
    for intent in analysis.intents:
        span = intent.evidence
        assert 0 <= span.char_start < span.char_end <= len(MESSAGE)
        print(f"  span: {MESSAGE[span.char_start:span.char_end]!r}")


def test_the_confidence_is_reported_as_the_model_s_own(outcome) -> None:  # type: ignore[no-untyped-def]
    """ADR-0050. If the model reports a number it is `provider_reported`; if not, `UNKNOWN`.

    Both are acceptable outcomes and the test says which happened rather than requiring one — what
    is not acceptable is a number appearing from nowhere and being presented as the model's.
    """
    analysis, _ = outcome
    for intent in analysis.intents:
        assert intent.confidence.source in (
            ConfidenceSource.PROVIDER_REPORTED,
            ConfidenceSource.UNAVAILABLE,
        ), "a real provider must not report a heuristic as its own confidence"
        if intent.confidence.band is ConfidenceBand.UNKNOWN:
            assert intent.confidence.value is None
        print(
            f"  confidence: {intent.confidence.band.value} "
            f"({intent.confidence.source.value}, value={intent.confidence.value})"
        )


def test_the_model_finds_the_promise(outcome) -> None:  # type: ignore[no-untyped-def]
    """The only assertion about behaviour, and it is deliberately weak.

    The message contains one unambiguous commitment. A model that finds nothing in it has either
    been given a broken prompt or is not doing the job — that is worth knowing, and it is the
    limit of what can be asserted about non-deterministic output without writing a flaky test.
    """
    analysis, _ = outcome
    print(f"  intents: {[i.kind.value for i in analysis.intents]}")
    print(f"  below_confidence={analysis.below_confidence} "
          f"inexpressible={analysis.inexpressible}")
    assert analysis.intents or analysis.below_confidence, (
        "the model produced nothing at all from a message containing a clear promise"
    )


# --------------------------------------------------------------------------- safety


def test_the_credential_appears_nowhere_in_the_result(outcome) -> None:  # type: ignore[no-untyped-def]
    """The check worth doing against the real thing.

    The unit tests assert this against a stubbed response we wrote. Here the response is the
    vendor's, and an adapter that echoed a header into an error message or a metadata field would
    show up here and nowhere else.
    """
    analysis, result = outcome
    rendered = f"{analysis!r}{result!r}"
    assert API_KEY not in rendered
    assert "x-api-key" not in rendered.lower()
    assert "authorization" not in rendered.lower()


def test_nothing_was_mutated(outcome) -> None:  # type: ignore[no-untyped-def]
    """`analyze` takes text and returns opinions. It has no session to mutate with.

    Asserted structurally rather than by counting rows: there is no database in this test at all,
    which is the strongest form the claim can take.
    """
    analysis, _ = outcome
    for intent in analysis.intents:
        for attribute in dir(intent):
            if attribute.startswith("_"):
                continue
            assert not callable(getattr(intent, attribute)), (
                f"ToolIntent.{attribute} is callable; an intent is a request, not an action"
            )
