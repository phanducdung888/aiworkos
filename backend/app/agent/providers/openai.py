"""An OpenAI adapter (ADR-0049).

Written over `httpx` and the Chat Completions API, for the same reasons the Anthropic adapter is: a
provider boundary should be a thin client, and adding an SDK would enlarge the dependency surface
for something that cannot run in CI anyway. What is left is one POST and a parse.

Everything vendor-neutral — the schema we ask for, span validation, confidence assessment — comes
from `structured`. What this file knows is what OpenAI specifically does: where the text lives in
the envelope, what it calls its stop reasons, and which field carries the token counts.

It is not on any default path. `FakeProvider` is what the runtime uses unless something explicitly
constructs this, and the test against the real endpoint is opt-in and off by default.

The credential never leaves this module: read from configuration, sent as a header, absent from
every type in `port.py`.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.agent.providers.errors import (
    ProviderAuthenticationFailure,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.agent.providers.port import (
    CompletionRequest,
    CompletionResult,
    FinishReason,
    ModelIdentity,
)
from app.agent.providers.structured import SCHEMA_INSTRUCTION, parse_spans

API_URL = "https://api.openai.com/v1/chat/completions"

#: OpenAI's stop reasons, mapped to this system's vocabulary.
#:
#: `content_filter` maps to `REFUSED` because that is what happened from the caller's point of
#: view: the model declined to answer. `tool_calls` maps to `OTHER` deliberately — this adapter
#: asks for no tools, so a response claiming tool calls means something is wrong with the request
#: rather than something worth interpreting.
_FINISH_REASONS = {
    "stop": FinishReason.COMPLETE,
    "length": FinishReason.LENGTH,
    "content_filter": FinishReason.REFUSED,
}


class OpenAIProvider:
    """`LLMProvider` over the Chat Completions API."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 2048,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            # Failing here rather than at the first call: a provider constructed without a
            # credential is a deployment mistake, and it should look like one at startup.
            raise ProviderAuthenticationFailure("no API key was configured")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._client = client

    def complete(self, request: CompletionRequest) -> CompletionResult:
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            # The current parameter name. Older models accept `max_tokens` instead and reject
            # this one; this adapter targets the current API rather than branching on a model
            # name it would have to keep guessing about. A deployment on an older model will see
            # a 400 and a `ProviderInvalidResponse`, which says so rather than failing obscurely.
            "max_completion_tokens": self._max_output_tokens,
            # Asks for a JSON object rather than hoping for one. `SCHEMA_INSTRUCTION` contains the
            # word "JSON", which this response format requires be present in the prompt.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": f"{request.instruction}\n\n{SCHEMA_INSTRUCTION}"},
                # The Event body is data, never instruction (BR-AI-10). It is the user message and
                # the system prompt above is the only source of task definition; anything inside
                # the body that looks like a command is content the model is told to treat as text.
                {"role": "user", "content": request.text},
            ],
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
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        if self._client is not None:
            return self._client.post(API_URL, json=payload, headers=headers)
        with httpx.Client(timeout=self._timeout) as client:
            return client.post(API_URL, json=payload, headers=headers)

    def _parse(
        self, body: dict[str, Any], request: CompletionRequest
    ) -> CompletionResult:
        choices = body.get("choices") or []
        if not choices:
            raise ProviderInvalidResponse("the provider returned no choices")
        choice = choices[0]

        # Where OpenAI keeps the text. The only vendor-specific part of reading this answer;
        # everything after it is shared with every other adapter.
        content = (choice.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ProviderInvalidResponse("the choice carried no text content")
        spans = parse_spans(
            content, source_text=request.text, max_spans=request.max_spans
        )

        model_name = str(body.get("model") or request.model or self._model)
        usage = body.get("usage") or {}
        return CompletionResult(
            spans=spans,
            model=ModelIdentity(
                provider=self.name,
                name=model_name,
                # Chat Completions reports the resolved model in `model` — often a dated variant
                # of what was requested — and no separate version field. The identifier is echoed
                # and marked unresolved rather than split into an invented version: a version
                # nobody can trust is worse than an absent one.
                version=model_name,
                resolved=False,
            ),
            finish_reason=_FINISH_REASONS.get(
                str(choice.get("finish_reason")), FinishReason.OTHER
            ),
            token_usage={
                "input": int(usage.get("prompt_tokens", 0)),
                "output": int(usage.get("completion_tokens", 0)),
            },
            provider_request_id=str(body.get("id")) if body.get("id") else None,
        )
