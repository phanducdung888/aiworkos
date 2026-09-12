"""Transactional outbox.

A domain event is written to `outbox` in the same transaction as the state change that produced it.
Nothing is published to any transport until that transaction commits, which is the whole point: it
removes the need for a distributed transaction without allowing phantom events.

This is infrastructure, not event sourcing. The current-state tables remain authoritative; the
outbox is a delivery mechanism with a bounded retention window (ADR-0005).

Phase 1 ships the append side and a relay with a no-op publisher. Redis Streams arrive in Phase 2
when there is a consumer; adding a transport now would be speculative.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.actor import Actor
from app.platform.ids import uuid7

logger = logging.getLogger(__name__)

_APPEND = text(
    """
    INSERT INTO outbox (
        id, org_id, occurred_at, type, aggregate_type, aggregate_id,
        payload, actor, correlation_id, causation_id
    ) VALUES (
        :id, :org_id, now(), :type, :aggregate_type, :aggregate_id,
        CAST(:payload AS jsonb), CAST(:actor AS jsonb), :correlation_id, :causation_id
    )
    """
)

# Ordered by `seq`, not by `id` and not by `occurred_at`. Ids are UUIDv7 whose sub-millisecond bits
# are random, so events appended inside one transaction have no order at all; `occurred_at` is
# `now()`, which in PostgreSQL is transaction start time and is therefore identical for every one of
# them. A sequence is the only column here that increases per row (migration 0006).
_CLAIM = text(
    """
    SELECT id, org_id, type, aggregate_type, aggregate_id, payload, actor,
           correlation_id, causation_id, attempts
    FROM outbox
    WHERE published_at IS NULL
    ORDER BY seq
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)

_MARK_PUBLISHED = text(
    "UPDATE outbox SET published_at = now(), attempts = attempts + 1 WHERE id = ANY(:ids)"
)

_MARK_FAILED = text(
    "UPDATE outbox SET attempts = attempts + 1, last_error = :error WHERE id = ANY(:ids)"
)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    id: uuid.UUID
    org_id: uuid.UUID
    type: str
    aggregate_type: str
    aggregate_id: uuid.UUID | None
    payload: dict[str, Any]


class Publisher(Protocol):
    def publish(self, events: list[DomainEvent]) -> None: ...


class LoggingPublisher:
    """Default publisher. Records that an event would have been dispatched.

    Deliberately inert: Phase 1 has no consumers, and a real transport with nothing listening is
    just a source of false confidence.
    """

    def publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            logger.info("domain event ready for dispatch: %s %s", event.type, event.id)


def append_domain_event(
    session: Session,
    *,
    org_id: uuid.UUID,
    type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID | None,
    payload: dict[str, Any],
    actor: Actor,
    correlation_id: str | None = None,
    causation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Append an event. Must be called inside the transaction that makes the change."""
    event_id = uuid7()
    session.execute(
        _APPEND,
        {
            "id": event_id,
            "org_id": org_id,
            "type": type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "payload": json.dumps(payload),
            "actor": json.dumps(actor.to_json()),
            "correlation_id": correlation_id or actor.request_id,
            "causation_id": causation_id,
        },
    )
    return event_id


def relay_once(session: Session, publisher: Publisher, *, limit: int = 100) -> int:
    """Claim a batch of unpublished events, publish them, mark them. Returns the count handled.

    Uses `FOR UPDATE SKIP LOCKED` so several relay workers can run without coordination. Delivery
    is at-least-once, so consumers must be idempotent.

    Ordering is per-transaction, not global. `seq` is assigned at insert but transactions commit in
    a different order than they started, so a relay reading between two commits can see a gap that
    fills in afterwards. Events from one transaction always arrive in the order they were appended,
    which is what causal ordering needs; a strict global order needs `pg_current_snapshot()` or an
    advisory lock, and is recorded in progress.md as a Phase 2 decision rather than guessed at here.
    """
    rows = session.execute(_CLAIM, {"limit": limit}).mappings().all()
    if not rows:
        return 0

    events = [
        DomainEvent(
            id=r["id"],
            org_id=r["org_id"],
            type=r["type"],
            aggregate_type=r["aggregate_type"],
            aggregate_id=r["aggregate_id"],
            payload=r["payload"] or {},
        )
        for r in rows
    ]
    ids = [e.id for e in events]
    try:
        publisher.publish(events)
    except Exception as exc:  # noqa: BLE001 - the relay must not die on a bad consumer
        session.execute(_MARK_FAILED, {"ids": ids, "error": str(exc)[:500]})
        logger.warning("outbox publish failed for %d events: %s", len(ids), exc)
        return 0
    session.execute(_MARK_PUBLISHED, {"ids": ids})
    return len(events)
