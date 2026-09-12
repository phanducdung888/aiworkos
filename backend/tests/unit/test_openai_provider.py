"""The OpenAI adapter, without OpenAI.

Every test drives a stubbed `httpx.Client`. The real API is never called: the behaviour worth
testing is the mapping — OpenAI's envelope becoming this system's types, transport failures
becoming typed errors, and the credential never appearing in anything the adapter returns.

An opt-in test against the real endpoint lives in `tests/smoke`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.agent.providers.errors import (
    ProviderAuthenticationFailure,
    ProviderContractViolation,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.agent.providers.openai import OpenAIProvider
from app.agent.providers.port import CompletionRequest, FinishReason
from app.platform.agentkit import ConfidenceBand, ConfidenceSource

TEXT = "Thanks for the call. I will send the revised quote on Friday."


def a_request(**over: Any) -> CompletionRequest:
    fields: dict[str, Any] = {
        "prompt_id": "extract.commitments",
        "prompt_version": "2026-09-12",
        "instruction": "Identify commitments.",
        "text": TEXT,
        "model": "gpt-4o",
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
    content = over.pop("content", json.dumps({"spans": spans}))
    body: dict[str, Any] = {
        "id": "chatcmpl-abc",
        "model": "gpt-4o-2024-11-20",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 130, "completion_tokens": 45},
    }
    body.update(over)
    return body


def client_returning(
    status: int = 200,
    body: dict[str, Any] | None = None,
    *,
    raises: Exception | None = None,
    capture: dict[str, Any] | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["headers"] = dict(request.headers)
            capture["payload"] = json.loads(request.content)
        if raises is not None:
            raise raises
        return httpx.Response(status, json=body if body is not None else a_body())

    return httpx.Client(transport=httpx.MockTransport(handler))


def provider(client: httpx.Client, **over: Any) -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key", model="gpt-4o", client=client, **over)


class TestConstruction:
    def test_a_missing_credential_fails_at_construction(self) -> None:
        """A deployment mistake should look like one at startup, not at the first request."""
        with pytest.raises(ProviderAuthenticationFailure):
            OpenAIProvider(api_key="   ", model="gpt-4o")


class TestRequestConstruction:
    def test_the_instruction_is_the_system_message_and_the_text_is_the_user_message(
        self,
    ) -> None:
        """BR-AI-10. The Event body is data; the system prompt is the only task definition.

        Putting the body in the system message would make anything inside it that reads like an
        instruction part of the task — which is precisely the injection this separation prevents.
        """
        seen: dict[str, Any] = {}
        provider(client_returning(capture=seen)).complete(a_request())
        messages = seen["payload"]["messages"]
        assert messages[0]["role"] == "system"
        assert "Identify commitments." in messages[0]["content"]
        assert messages[1] == {"role": "user", "content": TEXT}

    def test_it_asks_for_a_json_object(self) -> None:
        """And the prompt contains the word JSON, which the response format requires."""
        seen: dict[str, Any] = {}
        provider(client_returning(capture=seen)).complete(a_request())
        assert seen["payload"]["response_format"] == {"type": "json_object"}
        assert "JSON" in seen["payload"]["messages"][0]["content"]

    def test_it_sends_the_current_token_parameter(self) -> None:
        """`max_completion_tokens`, not `max_tokens`.

        The current API's name. An older model rejects it and returns 400, which surfaces as a
        `ProviderInvalidResponse` naming the status rather than failing obscurely — recorded as a
        known risk rather than papered over with a model-name branch.
        """
        seen: dict[str, Any] = {}
        provider(client_returning(capture=seen), max_output_tokens=512).complete(a_request())
        assert seen["payload"]["max_completion_tokens"] == 512
        assert "max_tokens" not in seen["payload"]

    def test_the_requested_model_is_sent(self) -> None:
        seen: dict[str, Any] = {}
        provider(client_returning(capture=seen)).complete(a_request(model="gpt-4o-mini"))
        assert seen["payload"]["model"] == "gpt-4o-mini"


class TestResponseNormalization:
    def test_it_parses_spans(self) -> None:
        result = provider(client_returning()).complete(a_request())
        assert len(result.spans) == 1
        span = result.spans[0]
        assert TEXT[span.char_start : span.char_end] == span.summary

    def test_it_reports_the_model_that_answered(self) -> None:
        """Not the one requested. Routing means those differ (ADR-0049)."""
        result = provider(client_returning()).complete(a_request(model="gpt-4o"))
        assert result.model.name == "gpt-4o-2024-11-20"
        assert result.model.provider == "openai"

    def test_it_does_not_invent_a_version(self) -> None:
        result = provider(client_returning()).complete(a_request())
        assert result.model.resolved is False
        assert result.model.version == result.model.name

    def test_usage_is_normalized_to_the_shared_names(self) -> None:
        """OpenAI says prompt/completion; this system says input/output. One vocabulary."""
        result = provider(client_returning()).complete(a_request())
        assert result.token_usage == {"input": 130, "output": 45}

    def test_the_request_id_is_carried(self) -> None:
        result = provider(client_returning()).complete(a_request())
        assert result.provider_request_id == "chatcmpl-abc"

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            ("stop", FinishReason.COMPLETE),
            ("length", FinishReason.LENGTH),
            ("content_filter", FinishReason.REFUSED),
            ("tool_calls", FinishReason.OTHER),
            ("something_new", FinishReason.OTHER),
        ],
    )
    def test_finish_reasons_are_mapped(
        self, reason: str, expected: FinishReason
    ) -> None:
        """An unmodelled reason is `OTHER`, not the nearest neighbour.

        Guessing what a vendor meant is how a contract rots.
        """
        body = a_body()
        body["choices"][0]["finish_reason"] = reason
        result = provider(client_returning(body=body)).complete(a_request())
        assert result.finish_reason is expected

    def test_a_model_confidence_is_labelled_as_the_model_s_own(self) -> None:
        result = provider(client_returning()).complete(a_request())
        assert result.spans[0].confidence.source is ConfidenceSource.PROVIDER_REPORTED
        assert result.spans[0].confidence.band is ConfidenceBand.HIGH

    def test_a_missing_confidence_is_unknown_not_a_guess(self) -> None:
        body = a_body(
            spans=[
                {
                    "kind": "commitment",
                    "summary": "x",
                    "char_start": 0,
                    "char_end": 1,
                }
            ]
        )
        result = provider(client_returning(body=body)).complete(a_request())
        assert result.spans[0].confidence.band is ConfidenceBand.UNKNOWN

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
        with pytest.raises(ProviderTimeout):
            provider(client_returning(raises=httpx.ReadTimeout("too slow"))).complete(
                a_request()
            )

    def test_a_transport_error_becomes_provider_unavailable(self) -> None:
        """No `httpx` exception reaches the runtime (ADR-0049)."""
        with pytest.raises(ProviderUnavailable):
            provider(client_returning(raises=httpx.ConnectError("no route"))).complete(
                a_request()
            )

    def test_no_choices_is_refused(self) -> None:
        with pytest.raises(ProviderInvalidResponse):
            provider(client_returning(body=a_body(choices=[]))).complete(a_request())

    def test_a_non_text_content_is_refused(self) -> None:
        """A refusal object, a tool call, or `null` — none of them is an answer we can read."""
        body = a_body()
        body["choices"][0]["message"]["content"] = None
        with pytest.raises(ProviderInvalidResponse):
            provider(client_returning(body=body)).complete(a_request())

    def test_unparseable_output_is_a_contract_failure_not_a_low_confidence(self) -> None:
        """The distinction the error hierarchy exists for (ADR-0049)."""
        with pytest.raises(ProviderInvalidResponse):
            provider(
                client_returning(body=a_body(content="I think they promised something?"))
            ).complete(a_request())

    def test_an_impossible_range_is_a_contract_violation(self) -> None:
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
    def test_the_key_is_sent_as_a_bearer_token_and_returned_nowhere(self) -> None:
        """There is no path by which it reaches an AIInteraction, an audit entry or a log."""
        seen: dict[str, Any] = {}
        result = OpenAIProvider(
            api_key="secret-key-value",
            model="gpt-4o",
            client=client_returning(capture=seen),
        ).complete(a_request())

        assert seen["headers"]["authorization"] == "Bearer secret-key-value"
        assert "secret-key-value" not in repr(result)
        assert "secret-key-value" not in str(result.model)
        assert "secret-key-value" not in str(result.token_usage)

    def test_the_key_is_absent_from_a_raised_error(self) -> None:
        """An adapter that put the request in an exception message would leak on every failure."""
        try:
            OpenAIProvider(
                api_key="secret-key-value",
                model="gpt-4o",
                client=client_returning(401, body={}),
            ).complete(a_request())
        except ProviderAuthenticationFailure as error:
            assert "secret-key-value" not in str(error)
            assert "secret-key-value" not in repr(error)
        else:  # pragma: no cover - the stub always returns 401
            pytest.fail("expected an authentication failure")
