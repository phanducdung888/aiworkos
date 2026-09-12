"""The FastAPI application.

Assembly only: mount the routers, install the problem+json handlers, expose a liveness endpoint.
Nothing here knows a business rule, and nothing here decides who may do what.

`/health` is deliberately outside `/api/v1` and outside authentication. A liveness probe that needs
a token cannot tell "the process is down" from "the identity provider is down", which is the one
distinction it exists to make.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1.dependencies import router as dependency_router
from app.api.v1.dependencies import work_scoped as work_dependency_router
from app.api.v1.events import router as events_router
from app.api.v1.identity import router as identity_router
from app.api.v1.projects import milestones as milestone_router
from app.api.v1.projects import projects as project_router
from app.api.v1.work import router as work_router
from app.platform.http import errors


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
    app.include_router(work_router)
    app.include_router(work_dependency_router)
    app.include_router(project_router)
    app.include_router(milestone_router)
    app.include_router(dependency_router)
    app.include_router(identity_router)
    app.include_router(events_router)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
