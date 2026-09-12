"""Transactional outbox.

The property under test is atomicity: a domain event exists if and only if the change that produced
it was committed. Everything else about the outbox is a delivery detail that can change; this one
cannot, because it is the reason the outbox exists instead of a direct publish (ADR-0005).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.actor import system_actor
from app.platform.outbox import DomainEvent, LoggingPublisher, append_domain_event, relay_once

pytestmark = pytest.mark.integration


def _scoped(session: Session, org_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[DomainEvent] = []

    def publish(self, events: list[DomainEvent]) -> None:
        self.published.extend(events)


class FailingPublisher:
    def publish(self, events: list[DomainEvent]) -> None:
        raise RuntimeError("transport unavailable")


def test_an_event_and_its_state_change_commit_together(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    person_id = uuid.uuid4()
    session.execute(
        text("INSERT INTO person (id, org_id, display_name) VALUES (:id, :org, 'Atomic')"),
        {"id": person_id, "org": org_a},
    )
    event_id = append_domain_event(
        session,
        org_id=org_a,
        type="PersonCreated",
        aggregate_type="person",
        aggregate_id=person_id,
        payload={"display_name": "Atomic"},
        actor=system_actor("outbox atomicity"),
    )
    session.commit()
    session.close()

    session = app_session_factory()
    _scoped(session, org_a)
    assert session.execute(
        text("SELECT count(*) FROM outbox WHERE id = :id"), {"id": event_id}
    ).scalar() == 1
    session.close()


def test_a_rolled_back_change_leaves_no_event(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """No phantom events. This is the failure a direct publish would produce."""
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    session.execute(
        text("INSERT INTO person (org_id, display_name) VALUES (:org, 'Doomed')"),
        {"org": org_a},
    )
    event_id = append_domain_event(
        session,
        org_id=org_a,
        type="PersonCreated",
        aggregate_type="person",
        aggregate_id=uuid.uuid4(),
        payload={},
        actor=system_actor("outbox rollback"),
    )
    session.rollback()
    session.close()

    session = app_session_factory()
    _scoped(session, org_a)
    assert session.execute(
        text("SELECT count(*) FROM outbox WHERE id = :id"), {"id": event_id}
    ).scalar() == 0
    assert session.execute(
        text("SELECT count(*) FROM person WHERE display_name = 'Doomed'")
    ).scalar() == 0
    session.close()


def test_the_relay_publishes_and_marks_events(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    for index in range(3):
        append_domain_event(
            session,
            org_id=org_a,
            type="WorkStatusChanged",
            aggregate_type="work",
            aggregate_id=uuid.uuid4(),
            payload={"index": index},
            actor=system_actor("relay"),
        )
    session.commit()
    session.close()

    publisher = RecordingPublisher()
    session = app_session_factory()
    _scoped(session, org_a)
    handled = relay_once(session, publisher, limit=10)
    session.commit()
    session.close()

    assert handled >= 3
    assert len(publisher.published) >= 3

    session = app_session_factory()
    _scoped(session, org_a)
    unpublished = session.execute(
        text("SELECT count(*) FROM outbox WHERE published_at IS NULL")
    ).scalar()
    assert unpublished == 0
    session.close()


def test_events_from_one_transaction_relay_in_the_order_they_were_appended(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """W-4, the reason `outbox.seq` exists (migration 0006).

    Ordering by `id` could not do this: ids are UUIDv7 and their sub-millisecond bits are random, so
    a batch appended inside one transaction came back shuffled. Ordering by `occurred_at` could not
    either — `now()` is transaction start time and is byte-identical for all of them. A consumer
    seeing `WorkCompleted` before the `WorkStatusChanged` that caused it is not a cosmetic problem;
    it is a consumer that cannot trust causality.

    Twenty events rather than two, because with two a shuffle is a coin flip that passes half the
    time.
    """
    org_a, _ = two_orgs
    aggregate_id = uuid.uuid4()
    expected = [f"Step{index:02d}" for index in range(20)]

    session = app_session_factory()
    _scoped(session, org_a)
    # Drain anything earlier tests left behind, so this assertion is about ordering and not about
    # which rows happen to be outstanding.
    relay_once(session, RecordingPublisher(), limit=1000)
    for event_type in expected:
        append_domain_event(
            session,
            org_id=org_a,
            type=event_type,
            aggregate_type="work",
            aggregate_id=aggregate_id,
            payload={},
            actor=system_actor("ordering"),
        )
    session.commit()
    session.close()

    publisher = RecordingPublisher()
    session = app_session_factory()
    _scoped(session, org_a)
    relay_once(session, publisher, limit=1000)
    session.commit()
    session.close()

    delivered = [e.type for e in publisher.published if e.aggregate_id == aggregate_id]
    assert delivered == expected


def test_a_failing_publisher_leaves_events_unpublished_and_records_the_error(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """At-least-once. A transport failure must not silently consume an event."""
    org_a, _ = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    event_id = append_domain_event(
        session,
        org_id=org_a,
        type="WorkCreated",
        aggregate_type="work",
        aggregate_id=uuid.uuid4(),
        payload={},
        actor=system_actor("relay failure"),
    )
    session.commit()
    session.close()

    session = app_session_factory()
    _scoped(session, org_a)
    handled = relay_once(session, FailingPublisher(), limit=10)
    session.commit()
    session.close()

    assert handled == 0
    session = app_session_factory()
    _scoped(session, org_a)
    row = session.execute(
        text("SELECT published_at, attempts, last_error FROM outbox WHERE id = :id"),
        {"id": event_id},
    ).mappings().one()
    assert row["published_at"] is None
    assert row["attempts"] == 1
    assert "transport unavailable" in row["last_error"]
    session.close()


def test_the_relay_sees_only_its_own_organization(
    app_session_factory: sessionmaker[Session], two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, org_b = two_orgs
    for org in (org_a, org_b):
        session = app_session_factory()
        _scoped(session, org)
        append_domain_event(
            session,
            org_id=org,
            type="WorkCreated",
            aggregate_type="work",
            aggregate_id=uuid.uuid4(),
            payload={},
            actor=system_actor("tenancy"),
        )
        session.commit()
        session.close()

    publisher = RecordingPublisher()
    session = app_session_factory()
    _scoped(session, org_a)
    relay_once(session, publisher, limit=100)
    session.commit()
    session.close()

    assert publisher.published
    assert {event.org_id for event in publisher.published} == {org_a}


def test_the_default_publisher_is_inert(caplog: pytest.LogCaptureFixture) -> None:
    """Phase 1 has no consumers. A real transport with nobody listening is false confidence."""
    event = DomainEvent(uuid.uuid4(), uuid.uuid4(), "WorkCreated", "work", None, {})
    LoggingPublisher().publish([event])
