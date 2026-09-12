"""The provider contract (ADR-0049).

A provider talks to a model and returns structured output. Most of this file is about the two
things Checkpoint 8's interface could not express: what a provider is allowed to *say*, and what
happens when it says something the contract cannot accept.

The second is the one worth reading. A malformed answer is a broken integration, not a
low-confidence result — and if the two are treated the same, a provider outage looks like a quiet
day, and nobody investigates a quiet day.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.agent.providers.errors import (
    ProviderAuthenticationFailure,
    ProviderContractViolation,
    ProviderError,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.agent.providers.fake import (
    HIGH_CONFIDENCE,
    LOW_CONFIDENCE,
    MALFORMED,
    MEDIUM_CONFIDENCE,
    TIMES_OUT,
    UNKNOWN_CONFIDENCE_SCENARIO,
    UNRESOLVED_VERSION,
    FakeProvider,
    FakeScenario,
)
from app.agent.providers.port import (
    CompletionRequest,
    CompletionResult,
    FinishReason,
    LLMProvider,
)
from app.platform.agentkit.confidence import ConfidenceBand, ConfidenceSource

PROMISE = "Thanks for the call. I will send the revised quote on Friday."


def ask(provider: FakeProvider, text: str = PROMISE, **over: object) -> object:
    request = CompletionRequest(
        prompt_id="extract.commitments",
        prompt_version="2026-09-12",
        instruction="Identify commitments.",
        text=text,
        model="deterministic",
        **over,  # type: ignore[arg-type]
    )
    return provider.complete(request)


class TestTheProtocol:
    def test_the_fake_satisfies_the_protocol(self) -> None:
        provider: LLMProvider = FakeProvider()
        assert provider.name == "fake"

    def test_it_finds_the_promise_in_the_text(self) -> None:
        """A real, if crude, extraction — not a canned answer.

        The spans have to index into the text because BR-E-05 checks the excerpt against the Event,
        and a mock returning fixed offsets would make that rule untestable.
        """
        result = ask(FakeProvider(HIGH_CONFIDENCE))
        assert result.spans  # type: ignore[attr-defined]
        span = result.spans[0]  # type: ignore[attr-defined]
        assert PROMISE[span.char_start : span.char_end] == span.summary

    def test_it_is_deterministic(self) -> None:
        first = ask(FakeProvider(HIGH_CONFIDENCE))
        second = ask(FakeProvider(HIGH_CONFIDENCE))
        assert [s.summary for s in first.spans] == [  # type: ignore[attr-defined]
            s.summary for s in second.spans  # type: ignore[attr-defined]
        ]

    def test_a_session_identifier_is_accepted_and_opaque(self) -> None:
        """The runtime owns conversation context; this is a token a provider may cache against.

        Accepted so that a provider needing one does not force the port to change, and carried
        nowhere — nothing downstream reads a provider's memory as truth (ADR-0049).
        """
        result = ask(FakeProvider(HIGH_CONFIDENCE), conversation_id="session-abc")
        assert result.spans  # type: ignore[attr-defined]


class TestModelIdentity:
    def test_it_reports_which_model_answered(self) -> None:
        result = ask(FakeProvider(HIGH_CONFIDENCE, model="deterministic", version="v3"))
        assert result.model.provider == "fake"  # type: ignore[attr-defined]
        assert result.model.name == "deterministic"  # type: ignore[attr-defined]
        assert result.model.version == "v3"  # type: ignore[attr-defined]
        assert result.model.resolved is True  # type: ignore[attr-defined]

    def test_an_unresolvable_version_says_so_rather_than_inventing_one(self) -> None:
        """"We do not know precisely" is visible rather than hidden behind a plausible string.

        A version nobody can trust is worse than an absent one, because BR-AI-32's promotion
        metrics would be computed over it.
        """
        result = ask(FakeProvider(UNRESOLVED_VERSION, model="routed"))
        assert result.model.resolved is False  # type: ignore[attr-defined]
        assert result.model.version == "routed"  # type: ignore[attr-defined]

    def test_the_finish_reason_is_reported(self) -> None:
        result = ask(FakeProvider(FakeScenario(finish_reason=FinishReason.LENGTH)))
        assert result.finish_reason is FinishReason.LENGTH  # type: ignore[attr-defined]


class TestConfidenceReporting:
    def test_high_medium_and_low_are_distinguishable(self) -> None:
        for scenario, expected in (
            (HIGH_CONFIDENCE, ConfidenceBand.HIGH),
            (MEDIUM_CONFIDENCE, ConfidenceBand.MEDIUM),
            (LOW_CONFIDENCE, ConfidenceBand.LOW),
        ):
            result = ask(FakeProvider(scenario))
            assert result.spans[0].confidence.band is expected  # type: ignore[attr-defined]

    def test_a_provider_reporting_nothing_produces_unknown(self) -> None:
        result = ask(FakeProvider(UNKNOWN_CONFIDENCE_SCENARIO))
        assessment = result.spans[0].confidence  # type: ignore[attr-defined]
        assert assessment.band is ConfidenceBand.UNKNOWN
        assert assessment.value is None

    def test_the_fake_labels_its_scores_as_heuristic(self) -> None:
        """A pattern match is an honest guess about whether a sentence is a promise.

        It is not a probability, and putting it in the same field as a model's own number without
        saying which is which is how a threshold stops meaning anything (ADR-0050).
        """
        result = ask(FakeProvider(HIGH_CONFIDENCE))
        assert (
            result.spans[0].confidence.source  # type: ignore[attr-defined]
            is ConfidenceSource.HEURISTIC
        )


class TestFailures:
    @pytest.mark.parametrize(
        ("error", "retryable"),
        [
            (ProviderUnavailable("down"), True),
            (ProviderTimeout("slow"), True),
            (ProviderRateLimited("too many"), True),
            (ProviderAuthenticationFailure("bad key"), False),
            (ProviderInvalidResponse("gibberish"), False),
            (ProviderContractViolation("out of range"), False),
        ],
    )
    def test_each_failure_is_typed_and_says_whether_retrying_helps(
        self, error: ProviderError, retryable: bool
    ) -> None:
        assert isinstance(error, ProviderError)
        assert error.retryable is retryable

    def test_a_configured_failure_is_raised_without_sleeping(self) -> None:
        """A test for timeout behaviour should not take as long as a timeout."""
        with pytest.raises(ProviderTimeout):
            ask(FakeProvider(TIMES_OUT))

    def test_a_malformed_answer_is_a_contract_failure_not_a_low_confidence(self) -> None:
        """The distinction the whole error hierarchy exists for.

        If a broken integration produced a low-confidence result, an outage would be
        indistinguishable from a day on which the model simply found nothing.
        """
        with pytest.raises(ProviderInvalidResponse):
            ask(FakeProvider(MALFORMED))

    def test_an_authentication_failure_is_not_retryable(self) -> None:
        """Asking again with the same rejected credential is the same request.

        It is an operational problem and should look like one rather than like a flaky model.
        """
        assert ProviderAuthenticationFailure("nope").retryable is False


# --------------------------------------------------------------------------- every adapter (CP13)


def _stub(body: dict[str, Any]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )


def _anthropic() -> tuple[Any, dict[str, Any]]:
    from app.agent.providers.anthropic import AnthropicProvider

    body = {
        "id": "msg_x",
        "model": "claude-sonnet-5-20260901",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "content": [{"type": "text", "text": json.dumps({"spans": _SPANS})}],
    }
    return AnthropicProvider(api_key="k", model="m", client=_stub(body)), body


def _openai() -> tuple[Any, dict[str, Any]]:
    from app.agent.providers.openai import OpenAIProvider

    body = {
        "id": "chatcmpl-x",
        "model": "gpt-4o-2024-11-20",
        "choices": [
            {
                "message": {"role": "assistant", "content": json.dumps({"spans": _SPANS})},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    return OpenAIProvider(api_key="k", model="m", client=_stub(body)), body


_SPANS = [
    {
        "kind": "commitment",
        "summary": "I will send the revised quote on Friday",
        "char_start": 21,
        "char_end": 60,
        "confidence": 90,
    }
]

#: Every adapter, exercised against the same contract.
#:
#: The point is not that each works — the per-vendor files cover that. It is that they produce the
#: *same shape* from different envelopes, because `AgentRuntime` is written against one contract
#: and a second provider is only useful if swapping it changes nothing above the boundary.
ADAPTERS = [pytest.param(_anthropic, id="anthropic"), pytest.param(_openai, id="openai")]


@pytest.mark.parametrize("build", ADAPTERS)
class TestEveryAdapterHonoursTheContract:
    def test_it_satisfies_the_protocol(self, build) -> None:  # type: ignore[no-untyped-def]
        adapter, _ = build()
        provider: LLMProvider = adapter
        assert isinstance(provider.name, str) and provider.name

    def test_it_returns_a_completion_result(self, build) -> None:  # type: ignore[no-untyped-def]
        adapter, _ = build()
        result = adapter.complete(ask_request())
        assert isinstance(result, CompletionResult)

    def test_it_normalizes_usage_to_input_and_output(self, build) -> None:  # type: ignore[no-untyped-def]
        """Vendors name these differently; this system does not."""
        adapter, _ = build()
        assert adapter.complete(ask_request()).token_usage == {"input": 100, "output": 20}

    def test_it_reports_the_model_that_answered(self, build) -> None:  # type: ignore[no-untyped-def]
        adapter, _ = build()
        result = adapter.complete(ask_request())
        assert result.model.name not in ("", "m"), "the requested model was echoed, not the actual"
        assert result.model.resolved is False

    def test_it_maps_a_normal_stop_to_complete(self, build) -> None:  # type: ignore[no-untyped-def]
        adapter, _ = build()
        assert adapter.complete(ask_request()).finish_reason is FinishReason.COMPLETE

    def test_it_reports_a_request_id(self, build) -> None:  # type: ignore[no-untyped-def]
        adapter, _ = build()
        assert adapter.complete(ask_request()).provider_request_id

    def test_it_labels_a_model_score_as_provider_reported(self, build) -> None:  # type: ignore[no-untyped-def]
        """ADR-0050. A number the model gave is the model's claim, whichever vendor relayed it."""
        adapter, _ = build()
        span = adapter.complete(ask_request()).spans[0]
        assert span.confidence.source is ConfidenceSource.PROVIDER_REPORTED
        assert span.confidence.band is ConfidenceBand.HIGH

    def test_its_spans_index_into_the_supplied_text(self, build) -> None:  # type: ignore[no-untyped-def]
        """BR-E-05, enforced identically because the enforcement is shared (CP13)."""
        adapter, _ = build()
        span = adapter.complete(ask_request()).spans[0]
        assert PROMISE[span.char_start : span.char_end] == span.summary


def ask_request() -> CompletionRequest:
    return CompletionRequest(
        prompt_id="extract.commitments",
        prompt_version="2026-09-12",
        instruction="Identify commitments.",
        text=PROMISE,
        model="m",
    )
