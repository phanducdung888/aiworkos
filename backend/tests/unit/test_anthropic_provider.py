"""The Anthropic adapter, without Anthropic.

Every test here drives a stubbed `httpx.Client`. The real API is never called: a suite that needs a
credential is a suite that gets skipped in the environment that should run it, and the behaviour
worth testing is the mapping — transport failures becoming typed errors, a malformed body becoming
a contract failure rather than a low confidence, and the credential never appearing in anything the
adapter returns.

An opt-in test against the real endpoint lives at the bottom and is off unless
`RUN_REAL_PROVIDER_TESTS=1`.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from app.agent.providers.anthropic import AnthropicProvider
from app.agent.providers.errors import (
    ProviderAuthenticationFailure,
    ProviderContractViolation,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.agent.providers.port import CompletionRequest, FinishReason
from app.platform.agentkit.confidence import ConfidenceBand, ConfidenceSource

TEXT = "Thanks for the call. I will send the revised quote on Friday."


def a_request(**over: Any) -> CompletionRequest:
    fields: dict[str, Any] = {
        "prompt_id": "extract.commitments",
        "prompt_version": "2026-09-12",
        "instruction": "Identify commitments.",
        "text": TEXT,
        "model": "claude-sonnet-5",
    }
    fields.update(over)
    return CompletionRequest(**fields)


def a_body(**over: Any) -> dict[str, Any]:
    spans = over.pop(
        "spans",
        [
            {
                "kind": "commitment",
                "summary": "I will send the revised quote on Friday",
                "char_start": 21,
                "char_end": 60,
                "confidence": 90,
            }
        ],
    )
    body: dict[str, Any] = {
        "id": "msg_abc",
        "model": "claude-sonnet-5-20260901",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 120, "output_tokens": 40},
        "content": [{"type": "text", "text": json.dumps({"spans": spans})}],
    }
    body.update(over)
    return body


def client_returning(
    status: int = 200, body: dict[str, Any] | None = None, *, raises: Exception | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if raises is not None:
            raise raises
        return httpx.Response(status, json=body if body is not None else a_body())

    return httpx.Client(transport=httpx.MockTransport(handler))


def provider(client: httpx.Client) -> AnthropicProvider:
    return AnthropicProvider(api_key="test-key", model="claude-sonnet-5", client=client)


class TestConstruction:
    def test_a_missing_credential_fails_at_construction(self) -> None:
        """A deployment mistake should look like one at startup, not at the first request."""
        with pytest.raises(ProviderAuthenticationFailure):
            AnthropicProvider(api_key="   ", model="claude-sonnet-5")


class TestSuccess:
    def test_it_parses_spans(self) -> None:
        result = provider(client_returning()).complete(a_request())
        assert len(result.spans) == 1
        span = result.spans[0]
        assert span.kind == "commitment"
        assert TEXT[span.char_start : span.char_end] == span.summary

    def test_it_reports_the_model_that_answered(self) -> None:
        """Not the one requested. Routing means those differ (ADR-0049)."""
        result = provider(client_returning()).complete(a_request(model="claude-sonnet-5"))
        assert result.model.name == "claude-sonnet-5-20260901"
        assert result.model.provider == "anthropic"

    def test_it_does_not_invent_a_version(self) -> None:
        """The Messages API reports no separate version, so the adapter says so.

        A version nobody can trust is worse than an absent one: BR-AI-32's promotion metrics would
        be computed over it.
        """
        result = provider(client_returning()).complete(a_request())
        assert result.model.resolved is False
        assert result.model.version == result.model.name

    def test_a_model_confidence_is_labelled_as_the_model_s_own(self) -> None:
        result = provider(client_returning()).complete(a_request())
        assert result.spans[0].confidence.source is ConfidenceSource.PROVIDER_REPORTED
        assert result.spans[0].confidence.band is ConfidenceBand.HIGH

    def test_a_missing_confidence_is_unknown_not_a_guess(self) -> None:
        body = a_body(
            spans=[
                {
                    "kind": "commitment",
                    "summary": "I will send the revised quote on Friday",
                    "char_start": 21,
                    "char_end": 60,
                }
            ]
        )
        result = provider(client_returning(body=body)).complete(a_request())
        assert result.spans[0].confidence.band is ConfidenceBand.UNKNOWN

    def test_a_non_numeric_confidence_is_unknown(self) -> None:
        """A confidence field that is not a number is not a confidence."""
        body = a_body(
            spans=[
                {
                    "kind": "commitment",
                    "summary": "x",
                    "char_start": 0,
                    "char_end": 1,
                    "confidence": "quite sure",
                }
            ]
        )
        result = provider(client_returning(body=body)).complete(a_request())
        assert result.spans[0].confidence.band is ConfidenceBand.UNKNOWN

    def test_usage_and_request_id_are_carried(self) -> None:
        result = provider(client_returning()).complete(a_request())
        assert result.token_usage == {"input": 120, "output": 40}
        assert result.provider_request_id == "msg_abc"

    def test_a_truncated_answer_reports_length(self) -> None:
        result = provider(client_returning(body=a_body(stop_reason="max_tokens"))).complete(
            a_request()
        )
        assert result.finish_reason is FinishReason.LENGTH

    def test_an_unmodelled_stop_reason_is_other_not_the_nearest_neighbour(self) -> None:
        """Guessing what a vendor meant is how a contract rots."""
        result = provider(client_returning(body=a_body(stop_reason="pause_turn"))).complete(
            a_request()
        )
        assert result.finish_reason is FinishReason.OTHER

    def test_max_spans_is_respected(self) -> None:
        many = [
            {"kind": "work", "summary": "x", "char_start": 0, "char_end": 1, "confidence": 90}
            for _ in range(10)
        ]
        result = provider(client_returning(body=a_body(spans=many))).complete(
            a_request(max_spans=3)
        )
        assert len(result.spans) == 3


class TestFailureMapping:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, ProviderAuthenticationFailure),
            (403, ProviderAuthenticationFailure),
            (429, ProviderRateLimited),
            (500, ProviderUnavailable),
            (503, ProviderUnavailable),
            (400, ProviderInvalidResponse),
        ],
    )
    def test_http_status_becomes_a_typed_error(
        self, status: int, expected: type[Exception]
    ) -> None:
        with pytest.raises(expected):
            provider(client_returning(status, body={})).complete(a_request())

    def test_a_timeout_becomes_a_provider_timeout(self) -> None:
        client = client_returning(raises=httpx.ReadTimeout("too slow"))
        with pytest.raises(ProviderTimeout):
            provider(client).complete(a_request())

    def test_a_transport_error_becomes_provider_unavailable(self) -> None:
        """No `httpx` exception reaches the runtime (ADR-0049).

        A domain that catches `httpx.HTTPError` is a domain with opinions about a transport, and
        the next adapter would need every `except` clause above it rewritten.
        """
        client = client_returning(raises=httpx.ConnectError("no route"))
        with pytest.raises(ProviderUnavailable):
            provider(client).complete(a_request())

    def test_unparseable_output_is_a_contract_failure_not_a_low_confidence(self) -> None:
        """The distinction the error hierarchy exists for."""
        body = a_body(content=[{"type": "text", "text": "I think they promised something?"}])
        with pytest.raises(ProviderInvalidResponse):
            provider(client_returning(body=body)).complete(a_request())

    def test_a_span_missing_fields_is_refused(self) -> None:
        body = a_body(spans=[{"kind": "commitment"}])
        with pytest.raises(ProviderInvalidResponse):
            provider(client_returning(body=body)).complete(a_request())

    def test_an_impossible_range_is_a_contract_violation(self) -> None:
        """Read the answer, and the answer was not allowed to say that."""
        body = a_body(
            spans=[
                {
                    "kind": "work",
                    "summary": "x",
                    "char_start": 50,
                    "char_end": 10,
                    "confidence": 90,
                }
            ]
        )
        with pytest.raises(ProviderContractViolation):
            provider(client_returning(body=body)).complete(a_request())


class TestCredentialHandling:
    def test_the_key_is_sent_as_a_header_and_returned_nowhere(self) -> None:
        """There is no path by which it reaches an AIInteraction, an audit entry or a log."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200, json=a_body())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = AnthropicProvider(
            api_key="secret-key-value", model="claude-sonnet-5", client=client
        ).complete(a_request())

        assert seen["x-api-key"] == "secret-key-value"
        assert "secret-key-value" not in repr(result)
        assert "secret-key-value" not in str(result.token_usage)
        assert "secret-key-value" not in str(result.model)


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_PROVIDER_TESTS") != "1",
    reason="opt-in: needs a real credential and network",
)
def test_the_real_endpoint_answers() -> None:  # pragma: no cover - opt-in
    """Off by default, on purpose.

    A suite that needs a credential is one that gets skipped in CI, which is the environment that
    should be running it. This exists so the adapter can be verified deliberately against the real
    API, not so that it is verified accidentally.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    assert key, "set ANTHROPIC_API_KEY to run this"
    result = AnthropicProvider(
        api_key=key, model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    ).complete(a_request())
    assert result.model.provider == "anthropic"
