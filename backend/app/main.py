"""The FastAPI application.

Assembly only: mount the routers, install the problem+json handlers, expose a liveness endpoint.
Nothing here knows a business rule, and nothing here decides who may do what.

`/health` is deliberately outside `/api/v1` and outside authentication. A liveness probe that needs
a token cannot tell "the process is down" from "the identity provider is down", which is the one
distinction it exists to make.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.agent.providers.anthropic import AnthropicProvider
from app.agent.providers.fake import FakeProvider
from app.agent.providers.openai import OpenAIProvider
from app.agent.providers.port import LLMProvider
from app.api.v1.agent import router as agent_router
from app.api.v1.commitments import router as commitments_router
from app.api.v1.dependencies import router as dependency_router
from app.api.v1.dependencies import work_scoped as work_dependency_router
from app.api.v1.events import router as events_router
from app.api.v1.evidence import router as evidence_router
from app.api.v1.identity import router as identity_router
from app.api.v1.projects import milestones as milestone_router
from app.api.v1.projects import projects as project_router
from app.api.v1.proposals import router as proposals_router
from app.api.v1.work import router as work_router
from app.platform.config import Settings, get_settings
from app.platform.http import errors


def build_provider(settings: Settings) -> LLMProvider:
    """The model provider this deployment runs with (ADR-0049).

    Assembly, and deliberately here rather than inside `app.agent`: a provider adapter knows
    nothing about configuration, and choosing between adapters is a deployment decision.

    The default is `fake`, so the real adapter is never on a default path — an environment that
    has not said which provider it wants does not quietly acquire one. An unknown name fails at
    startup rather than at the first analysis, and so does a provider named without a credential:
    both are deployment mistakes and should look like one before traffic arrives.
    """
    name = settings.llm_provider.strip().lower()
    if name in ("", "fake"):
        return FakeProvider()
    if name == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if name == "anthropic":
        return AnthropicProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise ValueError(f"unknown model provider: {settings.llm_provider!r}")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI WorkOS",
        version="0.1.0",
        description=(
            "Work Core HTTP API. Deterministic system of record: it works with AI switched off, "
            "and every entity the AI will later be able to propose is creatable and editable here "
            "by a human first."
        ),
        openapi_url="/api/v1/openapi.json",
    )
    errors.install(app)
    # Installed on the app rather than reached for in the router, so a test can replace it and a
    # deployment is configured in one place. `WORKOS_LLM_PROVIDER` was a setting nothing read
    # until CP24 needed a deployed process to talk to a real model.
    app.state.llm_provider = build_provider(get_settings())
    app.include_router(work_router)
    app.include_router(work_dependency_router)
    app.include_router(project_router)
    app.include_router(milestone_router)
    app.include_router(dependency_router)
    app.include_router(identity_router)
    app.include_router(events_router)
    app.include_router(evidence_router)
    app.include_router(commitments_router)
    app.include_router(proposals_router)
    app.include_router(agent_router)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
