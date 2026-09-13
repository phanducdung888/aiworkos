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

from connectors.imap.canonical import Attachment, CanonicalMessage

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
class AttachmentOutcome:
    """What became of the files a message carried."""

    delivered: int = 0
    failed: tuple[str, ...] = ()


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


    def deliver_attachments(
        self, event_id: str, attachments: tuple[Attachment, ...]
    ) -> AttachmentOutcome:
        """Put each file where the Event can find it, through the flow WorkOS published.

        Three calls per file and no shortcut between them (ADR-0039): reserve the attachment and
        receive a short-lived URL, PUT the bytes straight to the store, then tell WorkOS to record
        what the store actually holds. The bytes never pass through the API, which is the whole
        reason the flow has three steps instead of one.

        **The upload URL is used exactly as issued.** It is signed for a particular host, method and
        key, so there is nothing here to adjust and any adjustment would produce a 403 — which is
        the property that lets a connector hold no storage credential of its own.

        A file that fails is reported and the rest continue. An Event with three of its four
        attachments is a better record than no Event at all, and the one that failed is named.
        """
        delivered = 0
        failed: list[str] = []
        for attachment in attachments:
            try:
                ticket = self._request(
                    "POST",
                    f"/api/v1/events/{event_id}/attachments",
                    body={
                        "filename": attachment.filename,
                        "media_type": attachment.media_type,
                    },
                    # Derived from what is being uploaded, so a retried pass reserves the same
                    # attachment rather than a second copy of the same file.
                    key=f"{event_id}:{attachment.filename}:{attachment.size_bytes}",
                )
                self._upload(ticket["upload_url"], attachment)
                self._request(
                    "POST",
                    f"/api/v1/events/{event_id}/attachments/"
                    f"{ticket['attachment']['id']}/complete",
                )
            except (DeliveryRefused, DeliveryUnavailable, OSError) as error:
                logger.error(
                    "attachment %s of event %s was not delivered: %s",
                    attachment.filename,
                    event_id,
                    error,
                )
                failed.append(attachment.filename)
            else:
                delivered += 1
        return AttachmentOutcome(delivered=delivered, failed=tuple(failed))

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        """One authenticated call to WorkOS, retried on the answers worth retrying."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Organization-Id": self._organization_id,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key

        base = self._url.rsplit("/api/v1/events", 1)[0]
        last: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            request = urllib.request.Request(
                f"{base}{path}",
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers=headers,
            )
            try:
                with self._open(request, timeout=self._timeout) as response:
                    return dict(json.loads(response.read() or b"{}"))
            except urllib.error.HTTPError as error:
                status, payload = error.code, _read(error)
                if status not in _RETRYABLE_STATUS:
                    raise DeliveryRefused(status, _detail(payload)) from error
                last = DeliveryUnavailable(f"{status}: {_detail(payload)}")
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = error
            self._wait(attempt)
        raise DeliveryUnavailable(f"{method} {path} failed in {self._attempts} attempts: {last}")

    def _upload(self, url: str, attachment: Attachment) -> None:
        """PUT the bytes to the store, using the presigned URL exactly as it was issued.

        No `Authorization` header: the URL *is* the authority, and adding a WorkOS credential here
        would send it to a host that has no business seeing one.
        """
        request = urllib.request.Request(
            url,
            data=attachment.content,
            method="PUT",
            headers={"Content-Type": attachment.media_type},
        )
        with self._open(request, timeout=self._timeout) as response:
            if int(response.status) not in (200, 201, 204):
                raise DeliveryUnavailable(f"the store answered {response.status}")


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
