"""Delivering a canonical message to the WorkOS capture API.

Standard library only, on purpose. The connector runs beside a mail server, not beside WorkOS, and
its independence is easier to believe when it shares no package with the thing it posts to. It holds
a bearer token and an organization id; it holds no database credential, and there is nothing here
that could acquire one.

The whole surface is one endpoint. ADR-0058 put the seam at the HTTP API precisely so a connector
needs nothing else.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from connectors.imap.canonical import CanonicalMessage

logger = logging.getLogger("connectors.imap")

#: Retried, because they say "not now". Everything else says "not like this", and repeating a
#: malformed request is how a connector turns its own bug into somebody else's outage.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF = 0.5


class DeliveryRefused(Exception):
    """The API rejected the message and repeating it would not help."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class DeliveryUnavailable(Exception):
    """The API could not be reached, or kept saying "not now"."""


@dataclasses.dataclass(frozen=True, slots=True)
class Delivered:
    """What the API said happened.

    `created` distinguishes the first delivery from a redelivery: 201 for a new Event, 200 for one
    that already existed (BR-E-02). A connector uses it for nothing except its own log — the
    decision was WorkOS's, and both answers mean the message is safely recorded.
    """

    event_id: str
    created: bool
    revision_of: str | None


class IngestionClient:
    """Posts messages. Nothing else.

    The credential is held here and nowhere else in the connector, and it is never logged — an
    ingestion token grants `EVENT.CREATE` in one organization (ADR-0060), which is small, and a
    credential in a log file is still a credential.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        organization_id: str,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff: float = DEFAULT_BACKOFF,
        timeout: float = 30.0,
        opener: Any | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/v1/events"
        self._token = token
        self._organization_id = organization_id
        self._attempts = attempts
        self._backoff = backoff
        self._timeout = timeout
        # Injected so the retry behaviour is testable without a server, the same way the
        # application injects its token verifier rather than patching a module global.
        self._open = opener or urllib.request.urlopen

    def deliver(self, message: CanonicalMessage) -> Delivered:
        """Deliver one message, retrying only what is worth retrying.

        The idempotency key is the same on every attempt, which is the point: a retry after a
        timeout is the *same action*, and the API replays its first answer rather than recording a
        second Event (ADR-0058 §3).
        """
        payload = json.dumps(message.as_event()).encode()
        last: Exception | None = None

        for attempt in range(1, self._attempts + 1):
            try:
                status, body = self._post(payload, message.idempotency_key)
            except urllib.error.HTTPError as error:
                status, body = error.code, _read(error)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = error
                logger.warning(
                    "delivery of %s failed to reach the API (attempt %d/%d): %s",
                    message.source_ref,
                    attempt,
                    self._attempts,
                    error,
                )
                self._wait(attempt)
                continue

            if status in (200, 201):
                return Delivered(
                    event_id=body["id"],
                    created=status == 201,
                    revision_of=body.get("revision_of_event_id"),
                )
            if status in _RETRYABLE_STATUS:
                last = DeliveryUnavailable(f"{status}: {_detail(body)}")
                logger.warning(
                    "delivery of %s was deferred (attempt %d/%d): %s",
                    message.source_ref,
                    attempt,
                    self._attempts,
                    _detail(body),
                )
                self._wait(attempt)
                continue
            # 4xx that is not a "not now": the message is wrong, not the moment. Raised so the
            # caller can quarantine it instead of the connector retrying a defect forever.
            raise DeliveryRefused(status, _detail(body))

        raise DeliveryUnavailable(
            f"{message.source_ref} was not delivered in {self._attempts} attempts: {last}"
        )

    def _post(self, payload: bytes, key: str) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            self._url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
                "X-Organization-Id": self._organization_id,
                "Idempotency-Key": key,
            },
        )
        with self._open(request, timeout=self._timeout) as response:
            return response.status, json.loads(response.read() or b"{}")

    def _wait(self, attempt: int) -> None:
        time.sleep(self._backoff * (2 ** (attempt - 1)))


def _read(error: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        return json.loads(error.read() or b"{}")  # type: ignore[no-any-return]
    except ValueError:
        return {}


def _detail(body: dict[str, Any]) -> str:
    """The problem+json detail, or the rule that was broken. Never the whole body in a log."""
    rule = body.get("rule")
    detail = body.get("detail") or body.get("title") or "no detail"
    return f"{rule}: {detail}" if rule else str(detail)
