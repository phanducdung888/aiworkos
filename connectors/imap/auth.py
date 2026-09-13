"""How the connector proves who it is.

Standard library only, like the rest of the connector: it shares no package with the application it
posts to, and an OAuth client-credentials exchange is a form POST and a JSON response.

**Why this exists.** ADR-0058 says a connector is handed a bearer token by whoever deploys it, and
CP24 ran that arrangement against a real mailbox for an afternoon. The realm's access tokens live
fifteen minutes. The connector read the resulting 401 as "this message is wrong", marked it seen and
moved on — a message consumed from the mailbox and never delivered. CP24 fixed the misreading (a
rejected credential is now an outage, and the mail stays put) and left the renewal open, because
nothing that needs a human four times an hour can be dogfooded for a week.

So a token source is either something that can renew itself or something that cannot, and the
difference is visible in the type rather than discovered at runtime.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

logger = logging.getLogger("connectors.imap")


class Unauthenticated(Exception):
    """No usable credential: none was configured, or the identity provider refused this one."""


class TokenSource(Protocol):
    """Where the bearer token comes from.

    Two methods rather than one, because "give me a token" and "the one you gave me was rejected"
    are different questions. A caller that only had the first would have to guess whether a 401
    meant a stale cache or a revoked client.
    """

    def token(self) -> str:
        """A token believed to be valid. May be cached."""

    def renew(self) -> str:
        """Discard whatever is cached and obtain a new one, or raise `Unauthenticated`."""


class StaticToken:
    """A token minted elsewhere and passed in.

    Kept for the deployment that wants to hold the credential outside this process, and for every
    test that has a token and no identity provider. It cannot renew — saying so out loud is the
    point, because the alternative is a connector that looks healthy and stops delivering.
    """

    def __init__(self, value: str) -> None:
        self._value = value

    def token(self) -> str:
        return self._value

    def renew(self) -> str:
        raise Unauthenticated(
            "WORKOS_INGESTION_TOKEN was supplied directly and has been rejected; this connector "
            "cannot renew a token it did not obtain. Configure WORKOS_OIDC_TOKEN_URL, "
            "WORKOS_CLIENT_ID and WORKOS_CLIENT_SECRET for unattended operation."
        )

    def __repr__(self) -> str:  # pragma: no cover - a credential is not a debugging aid
        return "StaticToken(<redacted>)"


class ClientCredentials:
    """OAuth 2.0 client credentials against the realm's token endpoint (RFC 6749 §4.4).

    Cached until shortly before expiry, so a delivery pass costs no round trip in the common case
    and a token never expires between being read and being used. `leeway` is that "shortly": the
    gap has to cover the slowest request this token will be attached to, which is an attachment
    upload, not a capture.

    The secret is held here and nowhere else, is never logged, and is not in `repr`.
    """

    def __init__(
        self,
        token_url: str,
        *,
        client_id: str,
        client_secret: str,
        timeout: float = 15.0,
        leeway: float = 60.0,
        opener: Any | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self._url = token_url
        self._id = client_id
        self._secret = client_secret
        self._timeout = timeout
        self._leeway = leeway
        self._open = opener or urllib.request.urlopen
        self._clock = clock
        self._lock = threading.Lock()
        self._value: str | None = None
        self._expires_at = 0.0

    def token(self) -> str:
        with self._lock:
            if self._value is not None and self._clock() < self._expires_at:
                return self._value
            return self._fetch()

    def renew(self) -> str:
        with self._lock:
            self._value = None
            return self._fetch()

    def _fetch(self) -> str:
        """Called with the lock held."""
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._id,
                "client_secret": self._secret,
            }
        ).encode()
        request = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                payload: dict[str, Any] = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            # The provider's own words, which for a client-credentials refusal are
            # `invalid_client` or `unauthorized_client` — never the secret.
            raise Unauthenticated(
                f"the identity provider refused this client ({error.code}): "
                f"{_reason(error)}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise Unauthenticated(
                f"the identity provider could not be reached: {error}"
            ) from error

        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise Unauthenticated("the identity provider returned no access token")
        # A provider that does not say gets the shortest sane assumption rather than a long one:
        # being early is a wasted round trip, being late is a failed delivery.
        lifetime = payload.get("expires_in")
        seconds = float(lifetime) if isinstance(lifetime, (int, float)) else 60.0
        self._value = token
        self._expires_at = self._clock() + max(seconds - self._leeway, 0.0)
        # Said out loud, because §4 of CP25 asks that authentication be observable and because the
        # alternative is inferring it from the absence of 401s. No token, no secret, no claims —
        # only that one was obtained and roughly how long it is good for.
        logger.info(
            "obtained an access token for %s, valid for %ss (renewing %ss early)",
            self._id,
            int(seconds),
            int(self._leeway),
        )
        return token

    def __repr__(self) -> str:  # pragma: no cover
        return f"ClientCredentials(url={self._url!r}, client_id={self._id!r}, secret=<redacted>)"


def _reason(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read() or b"{}")
    except Exception:  # noqa: BLE001 - a provider that answers with prose is still an answer
        return error.reason if isinstance(error.reason, str) else "no detail"
    detail = body.get("error_description") or body.get("error") or "no detail"
    return str(detail)
