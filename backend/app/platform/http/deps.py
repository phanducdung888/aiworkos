"""Request-scoped dependencies.

This module exists so a router can ask for a session, a principal and an actor without knowing how
any of them is produced. It lives in `platform` rather than in `api` because producing them means
touching the database and the token verifier, which is exactly what contract §12 says a router does
not do. The router declares what it needs; this assembles it.

The whole chain runs once per request and in one order, because the order is the security model:

bearer token -> subject -> organization header -> membership -> roles -> principal
                                     |
                                     +-> RLS org context on the session

The session is bound to the claimed organization *before* membership is checked, which sounds
backwards and is not. It means the membership lookup is itself subject to RLS, so the query that
decides whether the caller belongs here cannot see outside the organization it is asking about.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.platform.actor import Actor, ActorType
from app.platform.auth import TokenError, TokenVerifier, build_verifier
from app.platform.authz import Principal
from app.platform.config import get_settings
from app.platform.db import session_factory, set_org_context
from app.platform.http.errors import AuthenticationRequired
from app.platform.http.validation import CleanText
from app.platform.principal import parse_organization_header, resolve_principal
from app.platform.storage import ObjectStore, S3ObjectStore


@lru_cache
def _verifier() -> TokenVerifier:
    return build_verifier()


def get_verifier(request: Request) -> TokenVerifier:
    """The app may carry its own verifier; otherwise build one from settings.

    Tests replace it on the app rather than by patching a module global, so a test that forgets to
    undo the replacement cannot leak into the next one.
    """
    override = getattr(request.app.state, "token_verifier", None)
    if isinstance(override, TokenVerifier):
        return override
    return _verifier()


def bearer_subject(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationRequired("a bearer token is required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return get_verifier(request).verify(token).subject
    except TokenError as exc:
        # Every rejection reads the same to the caller: expired, forged, wrong audience and wrong
        # issuer are four different facts to us and one fact to them.
        raise AuthenticationRequired("the bearer token was not accepted") from exc


def organization_id(
    x_organization_id: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    return parse_organization_header(x_organization_id)


def scoped_session(
    org_id: Annotated[uuid.UUID, Depends(organization_id)],
) -> Iterator[Session]:
    """One transaction per request, bound to the claimed organization.

    Commits when the handler returns, rolls back when it raises. A service that emitted an audit
    entry for a mutation that then failed would be a service that lies, so the two share a fate.
    """
    session = session_factory()()
    try:
        set_org_context(session, org_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_principal(
    session: Annotated[Session, Depends(scoped_session)],
    subject: Annotated[str, Depends(bearer_subject)],
    org_id: Annotated[uuid.UUID, Depends(organization_id)],
) -> Principal:
    return resolve_principal(session, subject=subject, org_id=org_id)


def current_actor(
    principal: Annotated[Principal, Depends(current_principal)],
    request: Request,
) -> Actor:
    return Actor(
        type=ActorType.PERSON,
        person_id=principal.person_id,
        request_id=request.headers.get("x-request-id"),
    )


#: Optional on every POST. Absent means "behave exactly as before", which is what keeps this from
#: being a breaking change to a client that has never heard of it. `CleanText` because the key is
#: persisted in a text column exactly like a body field, and arrived at the same 500 when it held a
#: NUL byte.
IdempotencyKeyDep = Annotated[CleanText | None, Header(alias="Idempotency-Key")]

def object_store(request: Request) -> ObjectStore:
    """The attachment store, or whatever the app was built with.

    Tests install `InMemoryObjectStore` on the app the same way they install a token verifier, so
    the capture path is exercised end to end without MinIO running — which is what lets these tests
    run anywhere, the way the OIDC tests already do with a generated realm (ADR-0039).
    """
    override = getattr(request.app.state, "object_store", None)
    if override is not None:
        return cast(ObjectStore, override)
    return _default_object_store()


@lru_cache
def _default_object_store() -> ObjectStore:
    settings = get_settings()
    return S3ObjectStore(
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        bucket=settings.s3_bucket,
        region=settings.s3_region,
        public_endpoint_url=settings.s3_public_endpoint_url or None,
    )


SessionDep = Annotated[Session, Depends(scoped_session)]
PrincipalDep = Annotated[Principal, Depends(current_principal)]
ActorDep = Annotated[Actor, Depends(current_actor)]
ObjectStoreDep = Annotated[ObjectStore, Depends(object_store)]
