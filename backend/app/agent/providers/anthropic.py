"""An Anthropic adapter (ADR-0049).

Written over `httpx`, which the project already depends on, rather than the vendor SDK. A provider
boundary should be a thin client: adding an SDK would enlarge the dependency surface for something
that cannot run in CI anyway, and the adapter would still be doing exactly this — one POST and a
parse.

It is not on any default path. `FakeProvider` is what the runtime uses unless something explicitly
constructs this, and the integration test against the real API is opt-in and off by default
(`RUN_REAL_PROVIDER_TESTS=1`). A test suite that needs a credential is a suite that gets skipped in
the environment that should run it.

The credential never leaves this module. It is read from configuration, sent as a header, and is
absent from every type in `port.py` — so there is no path by which it reaches an `AIInteraction`, an
audit entry or a log line.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.agent.providers.confidence import (
    UNKNOWN_CONFIDENCE,
    ConfidenceNormalizer,
    ConfidenceSource,
)
from app.agent.providers.errors import (
    ProviderAuthenticationFailure,
    ProviderContractViolation,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.agent.providers.port import (
    CompletionRequest,
    CompletionResult,
    ExtractedSpan,
    FinishReason,
    ModelIdentity,
)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

#: What the model is asked to return. Constrained to what BR-E-05 can verify: a span the excerpt
#: can be sliced from, not a summary that would read well and be unverifiable.
_SCHEMA_NOTE = (
    "Return JSON only: {\"spans\": [{\"kind\": \"commitment\"|\"work\", "
    "\"summary\": string, \"char_start\": int, \"char_end\": int, "
    "\"confidence\": int 0-100}]}. char_start and char_end must index the supplied text exactly. "
    "Return an empty list rather than guessing."
)

_FINISH_REASONS = {
    "end_turn": FinishReason.COMPLETE,
    "stop_sequence": FinishReason.COMPLETE,
    "max_tokens": FinishReason.LENGTH,
    "refusal": FinishReason.REFUSED,
}


class AnthropicProvider:
    """`LLMProvider` over the Messages API."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            # Failing here rather than at the first call: a provider constructed without a
            # credential is a deployment mistake, and it should look like one at startup.
            raise ProviderAuthenticationFailure("no API key was configured")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client = client
        self._normalizer = ConfidenceNormalizer()

    def complete(self, request: CompletionRequest) -> CompletionResult:
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "max_tokens": 2048,
            "system": f"{request.instruction}\n\n{_SCHEMA_NOTE}",
            # The Event body is data, never instruction (BR-AI-10). It is passed as user content
            # and the system prompt above is the only source of task definition; anything inside
            # the body that looks like a command is content the model is told to treat as text.
            "messages": [{"role": "user", "content": request.text}],
        }
        try:
            response = self._post(payload)
        except httpx.TimeoutException as error:
            raise ProviderTimeout(str(error)) from error
        except httpx.HTTPError as error:
            # Every transport failure becomes a typed provider error here, so no `httpx` exception
            # reaches the runtime (ADR-0049).
            raise ProviderUnavailable(str(error)) from error

        if response.status_code in (401, 403):
            raise ProviderAuthenticationFailure("the provider rejected the credential")
        if response.status_code == 429:
            raise ProviderRateLimited("the provider is rate limiting this client")
        if response.status_code >= 500:
            raise ProviderUnavailable(f"provider returned {response.status_code}")
        if response.status_code != 200:
            raise ProviderInvalidResponse(f"provider returned {response.status_code}")

        return self._parse(response.json(), request)

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            # The one place the credential appears. It is never returned, never logged and never
            # part of any type in `port.py`.
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        if self._client is not None:
            return self._client.post(API_URL, json=payload, headers=headers)
        with httpx.Client(timeout=self._timeout) as client:
            return client.post(API_URL, json=payload, headers=headers)

    def _parse(
        self, body: dict[str, Any], request: CompletionRequest
    ) -> CompletionResult:
        blocks = body.get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        )
        try:
            parsed = json.loads(text)
            raw_spans = parsed["spans"]
        except (ValueError, KeyError, TypeError) as error:
            # A malformed answer is a broken contract, not a low confidence (ADR-0049). Treating
            # them the same would make an outage indistinguishable from a quiet day.
            raise ProviderInvalidResponse(
                "the provider did not return the requested JSON shape"
            ) from error

        spans: list[ExtractedSpan] = []
        for raw in raw_spans[: request.max_spans]:
            try:
                start, end = int(raw["char_start"]), int(raw["char_end"])
                kind, summary = str(raw["kind"]), str(raw["summary"])
            except (KeyError, TypeError, ValueError) as error:
                raise ProviderInvalidResponse("a span was missing required fields") from error
            if not 0 <= start < end:
                raise ProviderContractViolation(
                    f"span [{start}:{end}] is not a range"
                )
            spans.append(
                ExtractedSpan(
                    kind=kind,
                    summary=summary,
                    char_start=start,
                    char_end=end,
                    # The model's own number, labelled as the model's own number. A model's
                    # self-reported confidence is a value it produced, not a measured frequency —
                    # which is what `PROVIDER_REPORTED` says and `HEURISTIC` would not (ADR-0050).
                    confidence=self._assess(raw.get("confidence")),
                )
            )

        model_name = str(body.get("model") or request.model or self._model)
        return CompletionResult(
            spans=tuple(spans),
            model=ModelIdentity(
                provider=self.name,
                name=model_name,
                # The Messages API reports the resolved model in `model` and no separate version.
                # The canonical identifier is echoed and marked unresolved rather than invented —
                # a version nobody can trust is worse than an absent one.
                version=model_name,
                resolved=False,
            ),
            finish_reason=_FINISH_REASONS.get(
                str(body.get("stop_reason")), FinishReason.OTHER
            ),
            token_usage={
                "input": int(body.get("usage", {}).get("input_tokens", 0)),
                "output": int(body.get("usage", {}).get("output_tokens", 0)),
            },
            provider_request_id=str(body.get("id")) if body.get("id") else None,
        )

    def _assess(self, value: Any) -> Any:
        if value is None:
            return UNKNOWN_CONFIDENCE
        try:
            return self._normalizer.from_value(
                int(value), source=ConfidenceSource.PROVIDER_REPORTED
            )
        except (TypeError, ValueError):
            # A confidence field that is not a number is not a confidence. Unknown rather than
            # coerced: inventing a score at the value that decides the outcome is the failure
            # ADR-0050 exists to prevent.
            return UNKNOWN_CONFIDENCE
