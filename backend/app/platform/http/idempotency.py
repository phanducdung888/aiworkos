"""Safe retries for POST.

A client that times out mid-request knows nothing: the Work item may exist, or may not. Without a
way to ask, its only options are to retry and risk a duplicate, or not to retry and risk losing the
write. An idempotency key turns that into a third option — retry and get the first answer back.

Two properties carry the whole feature, and dropping either makes it decorative:

**The record commits with the mutation.** Written through the request's own session, so it lands in
the same transaction. Storing it afterwards would mean it only ever remembers requests that already
succeeded, which excludes exactly the case a client retries: the one where nobody knows what
happened.

**The same key with a different body is an error, not a replay.** Returning the first response to a
second, different request would silently discard the second one. The key identifies an attempt at
one operation, not a licence to skip any later call that reuses it.

The hash covers the endpoint, the organization and the canonical body. Headers are excluded on
purpose: a retry that differs only in `User-Agent` or a trace header is the same request, and
hashing them would make every retry look new.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.platform.ids import uuid7


class IdempotencyKeyReused(Exception):
    """The key has already been used for a different request on this endpoint."""


class ConcurrentRequest(Exception):
    """Two requests carrying the same key are in flight at once.

    The unique constraint catches the second one at insert time, after its mutation has run in the
    same transaction — so that transaction rolls back and nothing is persisted twice. The caller
    retries and gets the first request's stored answer.
    """


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status: int
    body: Any


_LOOKUP = text(
    """
    SELECT request_hash, response_status, response_body
    FROM idempotency_key
    WHERE org_id = :org_id AND key = :key AND endpoint = :endpoint
    """
)

_INSERT = text(
    """
    INSERT INTO idempotency_key (
        id, org_id, key, endpoint, request_hash, response_status, response_body
    ) VALUES (
        :id, :org_id, :key, :endpoint, :request_hash, :response_status,
        CAST(:response_body AS jsonb)
    )
    """
)


def request_hash(*, endpoint: str, org_id: uuid.UUID, payload: Any) -> str:
    """A stable fingerprint of what was asked.

    `sort_keys` so that two JSON objects differing only in field order hash the same — a client
    re-serialising its retry from a dict must not look like a different request.
    """
    canonical = json.dumps(
        {"endpoint": endpoint, "org_id": str(org_id), "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class Idempotency:
    """One request's worth of replay protection. Inert when the caller supplied no key."""

    def __init__(
        self,
        session: Session,
        *,
        org_id: uuid.UUID,
        endpoint: str,
        key: str | None,
        payload: Any,
    ) -> None:
        self._session = session
        self._org_id = org_id
        self._endpoint = endpoint
        self._key = key.strip() if key and key.strip() else None
        self._hash = (
            request_hash(endpoint=endpoint, org_id=org_id, payload=payload)
            if self._key
            else ""
        )

    @property
    def active(self) -> bool:
        return self._key is not None

    def replay(self) -> StoredResponse | None:
        """The stored answer for this exact request, or None if this is the first time.

        Raises when the key has been seen with a different body, because answering a question with
        the reply to a different one is worse than refusing.
        """
        if self._key is None:
            return None
        row = self._session.execute(
            _LOOKUP,
            {"org_id": self._org_id, "key": self._key, "endpoint": self._endpoint},
        ).mappings().one_or_none()
        if row is None:
            return None
        if row["request_hash"] != self._hash:
            raise IdempotencyKeyReused(
                "this Idempotency-Key was already used for a different request on this endpoint"
            )
        return StoredResponse(status=row["response_status"], body=row["response_body"])

    def record(self, status: int, body: Any) -> None:
        """Store the response, in the transaction that produced it."""
        if self._key is None:
            return
        try:
            with self._session.begin_nested():
                self._session.execute(
                    _INSERT,
                    {
                        "id": uuid7(),
                        "org_id": self._org_id,
                        "key": self._key,
                        "endpoint": self._endpoint,
                        "request_hash": self._hash,
                        "response_status": status,
                        "response_body": json.dumps(body, default=str),
                    },
                )
        except IntegrityError as exc:
            raise ConcurrentRequest(
                "another request with this Idempotency-Key is already in flight"
            ) from exc


def replay_response(stored: StoredResponse) -> JSONResponse:
    """The first response, returned verbatim.

    The ETag is rebuilt from the stored body's `version` rather than stored separately. The body is
    the resource, so its version is the entity tag by definition; keeping one copy of that fact
    means a replay cannot disagree with the payload it is attached to. A body with no `version` —
    nothing today, but the shape allows it — simply gets no tag.
    """
    headers: dict[str, str] = {"Idempotent-Replay": "true"}
    if isinstance(stored.body, dict) and isinstance(stored.body.get("version"), int):
        headers["ETag"] = f'W/"{stored.body["version"]}"'
    return JSONResponse(status_code=stored.status, content=stored.body, headers=headers)
