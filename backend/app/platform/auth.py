"""Access-token verification.

Bearer JWTs issued by Keycloak, validated in-process against its JWKS on every request: signature,
issuer, audience and expiry (security-model §2). There is deliberately no introspection call and no
token cache — the only thing cached is the signing key set, which is public.

The application consumes three claims and no more: `sub`, `email` and whatever coarse platform
roles the realm carries. It never reads an organization from the token. Organization membership is
resolved from local rows (`app/platform/principal.py`) because resource-scoped permissions change
far more often than identity does, and because a token that carried them would be a token that
grants access after the grant was revoked.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.platform.config import get_settings


class TokenError(Exception):
    """The token is missing, malformed, expired, or not one we issued to ourselves.

    One error rather than several on purpose. "Expired" and "wrong audience" are different facts to
    a developer and the same fact to a caller, and telling an unauthenticated caller which of the
    two they got wrong is free reconnaissance.
    """


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    email: str | None
    raw: dict[str, Any]


#: How a JWKS document is fetched. Injected so tests can supply a key set without a network call or
#: a running identity provider (testing-strategy T-3, resolved as option (a)).
JwksFetcher = Callable[[str], dict[str, Any]]


def _http_fetch(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    return body


class JwksCache:
    """The realm's public signing keys, cached with a TTL and invalidated by an unknown `kid`.

    The TTL bounds staleness; the unknown-kid refetch is what makes a rotation take effect at once
    rather than up to a TTL later. Without the second rule, every key rotation would produce a
    window of rejected-but-valid tokens.
    """

    def __init__(
        self,
        url: str,
        *,
        fetch: JwksFetcher = _http_fetch,
        ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None

    def signing_key(self, kid: str | None) -> jwt.PyJWK:
        if kid is None:
            raise TokenError("the token header carries no key id")
        if self._stale() or kid not in self._keys:
            self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise TokenError("the token was signed by an unknown key")
        return key

    def _stale(self) -> bool:
        if self._fetched_at is None:
            return True
        return (self._clock() - self._fetched_at) > self._ttl

    def _refresh(self) -> None:
        try:
            document = self._fetch(self._url)
        except Exception as exc:  # noqa: BLE001 - any transport failure is the same to a caller
            raise TokenError("the signing keys could not be retrieved") from exc
        self._keys = {
            key.key_id: key
            for key in jwt.PyJWKSet.from_dict(document).keys
            if key.key_id is not None
        }
        self._fetched_at = self._clock()


class TokenVerifier:
    """Verifies one realm's tokens. Construct once per process; it holds the key cache."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks: JwksCache,
        algorithms: tuple[str, ...] = ("RS256",),
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks = jwks
        self._algorithms = list(algorithms)

    def verify(self, token: str) -> TokenClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenError("the token is not a well-formed JWT") from exc

        # The algorithm comes from our list, never from the token's own header: honouring the
        # header is how `alg: none` and HMAC-with-the-public-key forgeries work.
        key = self._jwks.signing_key(header.get("kid"))
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenError("the token is not valid for this application") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise TokenError("the token carries no subject")
        email = claims.get("email")
        return TokenClaims(
            subject=subject,
            email=email if isinstance(email, str) else None,
            raw=claims,
        )


def build_verifier() -> TokenVerifier:
    settings = get_settings()
    return TokenVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks=JwksCache(
            settings.oidc_jwks_url, ttl_seconds=settings.oidc_jwks_ttl_seconds
        ),
    )
