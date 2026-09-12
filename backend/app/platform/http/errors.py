"""RFC 9457 problem responses.

One translation table, in one place, so that no router decides for itself what a rule violation
looks like on the wire. Every exception the application raises deliberately has a status here; an
exception that reaches the generic handler is a bug, and it renders as an opaque 500 rather than a
stack trace, because a stack trace is a description of the schema.

`type` is a URN, not a URL. A URL would be a promise to serve documentation at a domain nobody has
registered, and a broken link in a machine-readable field is worse than a stable opaque identifier.

The 404 body says nothing at all about what was asked for. It is returned both for "does not exist"
and for "exists in another organization", and those two must be indistinguishable down to the byte:
a message that echoed the resource's title, or even confirmed the shape of what was found, would
answer a question about another tenant (contract §5).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.platform.authz import AuthorizationError
from app.platform.authz.agent import AgentAuthorityError
from app.platform.concurrency import StaleVersionError
from app.platform.errors import DomainRuleViolation, EntityNotFound
from app.platform.http.idempotency import ConcurrentRequest, IdempotencyKeyReused
from app.platform.http.pagination import InvalidCursor
from app.platform.principal import (
    NoRolesHeld,
    OrganizationContextRequired,
    OrganizationNotResolvable,
)

logger = logging.getLogger(__name__)

CONTENT_TYPE = "application/problem+json"

URN_PREFIX = "urn:workos:error:"


class PreconditionRequired(Exception):
    """A mutation of an existing entity arrived without `If-Match`."""


class AuthenticationRequired(Exception):
    """No bearer token, or one we will not accept."""


def problem(
    status: int,
    slug: str,
    title: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{URN_PREFIX}{slug}",
        "title": title,
        "status": status,
        "detail": detail,
        **extra,
    }
    return JSONResponse(
        status_code=status, content=body, media_type=CONTENT_TYPE, headers=headers
    )


def _not_found() -> JSONResponse:
    """Deliberately constant. See the module docstring."""
    return problem(
        404,
        "not-found",
        "Not found",
        "No such resource is visible in this organization.",
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainRuleViolation)
    async def _rule(_: Request, exc: DomainRuleViolation) -> JSONResponse:
        # 422 rather than 400: the request was understood and well-formed, and the business rules
        # refused it. `rule` carries the id so a client can act on the rule rather than the prose.
        return problem(
            422,
            "rule-violation",
            "Business rule violation",
            str(exc),
            rule=exc.rule,
        )

    @app.exception_handler(AuthorizationError)
    async def _forbidden(_: Request, exc: AuthorizationError) -> JSONResponse:
        return problem(403, "forbidden", "Forbidden", str(exc))

    @app.exception_handler(AgentAuthorityError)
    async def _agent_refused(_: Request, exc: AgentAuthorityError) -> JSONResponse:
        """An agent tried something outside its authority (BR-AI-03).

        403 rather than 422: this is not a malformed request but a refused one, and the caller
        cannot fix it by sending different values. The refusal is also recorded as a `tool_call`
        with `authorization_result = denied`, which is the row an auditor reads.
        """
        return problem(403, "forbidden", "Forbidden", str(exc))

    @app.exception_handler(NoRolesHeld)
    async def _no_roles(_: Request, exc: NoRolesHeld) -> JSONResponse:
        return problem(403, "forbidden", "Forbidden", str(exc))

    @app.exception_handler(EntityNotFound)
    async def _missing(_: Request, __: EntityNotFound) -> JSONResponse:
        return _not_found()

    @app.exception_handler(OrganizationNotResolvable)
    async def _no_org(_: Request, __: OrganizationNotResolvable) -> JSONResponse:
        return _not_found()

    @app.exception_handler(OrganizationContextRequired)
    async def _org_required(_: Request, exc: OrganizationContextRequired) -> JSONResponse:
        return problem(
            400,
            "organization-context-required",
            "Organization context required",
            str(exc),
        )

    @app.exception_handler(StaleVersionError)
    async def _stale(_: Request, __: StaleVersionError) -> JSONResponse:
        return problem(
            412,
            "stale-version",
            "Precondition failed",
            "The resource has changed since the version you supplied. Re-read it and reapply "
            "the change.",
        )

    @app.exception_handler(PreconditionRequired)
    async def _precondition(_: Request, __: PreconditionRequired) -> JSONResponse:
        return problem(
            428,
            "precondition-required",
            "Precondition required",
            "This request must carry an If-Match header holding the ETag you last read.",
        )

    @app.exception_handler(IdempotencyKeyReused)
    async def _key_reused(_: Request, exc: IdempotencyKeyReused) -> JSONResponse:
        # 422 rather than 409: the request is well-formed and the conflict is with its own key, not
        # with the state of a resource.
        return problem(
            422,
            "idempotency-key-reused",
            "Idempotency key reused",
            str(exc),
        )

    @app.exception_handler(ConcurrentRequest)
    async def _concurrent(_: Request, exc: ConcurrentRequest) -> JSONResponse:
        # Nothing was persisted: the mutation shared the transaction that just rolled back. The
        # retry this suggests will find the other request's stored response and replay it.
        return problem(
            409,
            "concurrent-request",
            "Concurrent request",
            str(exc),
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(InvalidCursor)
    async def _cursor(_: Request, exc: InvalidCursor) -> JSONResponse:
        return problem(400, "invalid-cursor", "Invalid cursor", str(exc))

    @app.exception_handler(AuthenticationRequired)
    async def _unauthenticated(_: Request, exc: AuthenticationRequired) -> JSONResponse:
        return problem(
            401,
            "authentication-required",
            "Authentication required",
            str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(
            422,
            "validation-failed",
            "Request validation failed",
            "The request body or query string does not match the expected shape.",
            errors=[
                {"loc": list(error["loc"]), "msg": error["msg"]} for error in exc.errors()
            ],
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        """A constraint the request violated, reported without describing the constraint.

        The database is the last line of several, and reaching it means a caller referenced
        something that does not exist or supplied a value the schema forbids. That is a client
        error, not a server one, so 500 would be wrong — and psycopg's message names the table, the
        column and the constraint, which is a description of the schema and goes to the log instead
        (contract §5).
        """
        logger.warning("integrity error surfaced to the API boundary: %s", exc)
        return problem(
            422,
            "constraint-violation",
            "Constraint violation",
            "The request references something that does not exist, or a value this resource "
            "does not accept.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Starlette's own errors, rendered the same way ours are.

        Routing failures — an unknown path, a method the route does not have — are raised by the
        framework before any handler runs, and its default renders them as `application/json` with
        a `detail` string. Left alone, this API would answer 404 in two different shapes depending
        on whether the miss was a route or a row, and a client cannot write one error path against
        two contracts.
        """
        slugs = {404: "not-found", 405: "method-not-allowed", 401: "authentication-required"}
        titles = {404: "Not found", 405: "Method not allowed", 401: "Authentication required"}
        status = exc.status_code
        return problem(
            status,
            slugs.get(status, "request-failed"),
            titles.get(status, "Request failed"),
            # Never the framework's detail for a 404: "Not Found" is harmless, but the same code
            # path serves resource misses, and those must say nothing at all (contract §5).
            "No such resource is visible in this organization."
            if status == 404
            else str(exc.detail),
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Logged in full, reported as nothing. Whatever went wrong is ours to read and not the
        # caller's to learn from.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return problem(
            500,
            "internal",
            "Internal server error",
            "The request could not be completed.",
        )
