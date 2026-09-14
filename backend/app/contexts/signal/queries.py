"""Read paths for Signal/Capture.

The one piece of real logic here is BR-E-08: a `restricted` Event reaches only its participants and
an organization auditor. That is applied as a SQL predicate rather than as an authorization grant
because the policy engine never reads the database, and deciding "is this person a participant"
means reading `event_participant`. Work visibility is arranged the same way for the same reason.

The predicate is the only place the rule is spelled out for reading.
`domain.visible_to` states it for anything that has already loaded an Event, and
`tests/unit/test_signal_domain.py` pins the two to the same answer.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Select, and_, exists, or_, select
from sqlalchemy.orm import Session

from app.contexts.signal.domain import EventType, Sensitivity
from app.contexts.signal.models import Event, EventParticipant
from app.platform.authz import Action, Grant, Principal, ResourceType, Role, grants_for
from app.platform.http.pagination import Cursor, encode_cursor

#: Roles that see a `restricted` Event without being named on it. BR-E-08 names the auditor and
#: nobody else — an `org_admin` administers the organization, which is not the same as being
#: entitled to read a conversation somebody marked restricted.
SENSITIVITY_EXEMPT_ROLES = frozenset({Role.AUDITOR})


@dataclasses.dataclass(frozen=True, slots=True)
class EventFilter:
    event_type: EventType | None = None
    source_system: str | None = None
    occurred_after: dt.datetime | None = None
    occurred_before: dt.datetime | None = None
    participant_person_id: uuid.UUID | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class EventPage:
    items: list[Event]
    next_cursor: str | None


def _participates(person_id: uuid.UUID) -> Any:
    return exists().where(
        and_(
            EventParticipant.event_id == Event.id,
            EventParticipant.org_id == Event.org_id,
            EventParticipant.person_id == person_id,
        )
    )


def _sensitivity_predicate(principal: Principal) -> Any:
    """BR-E-08, and nothing more than BR-E-08.

    `confidential` is not narrowed. The rule names one tier and enforcing a second would make the
    label mean something no rule states — a reader would have to consult the code to find out what
    marking an Event confidential actually does.
    """
    if principal.roles & SENSITIVITY_EXEMPT_ROLES:
        return None
    if principal.person_id is None:
        # A service account with no Person cannot be a participant, so it cannot reach a restricted
        # Event by any route. Saying so explicitly beats letting the subquery compare against NULL.
        return Event.sensitivity != Sensitivity.RESTRICTED.value
    return or_(
        Event.sensitivity != Sensitivity.RESTRICTED.value,
        _participates(principal.person_id),
    )


def _reach_predicate(principal: Principal) -> Any:
    """The matrix's grants for (EVENT, READ), rendered as SQL.

    Ordered widest first, and the final clause is the one that matters. Until CP23 an unrecognised
    grant fell through to `None` — no `WHERE` clause at all — so a role granted `PERSONAL` would
    have read every Event in the organization while the matrix said it read only its own. A
    narrowing that silently widens is worse than no narrowing, because the table stops describing
    the system.

    `PERSONAL` means the Events this principal captured, which is the same relation
    `authorization.event_relations` uses to decide `ATTACH`. A connector delivering messages holds
    exactly this (ADR-0060, ADR-0063): it may read back what it delivered and nothing else.
    """
    grants = grants_for(principal, Action.READ, ResourceType.EVENT)
    if Grant.ORG in grants:
        return None
    if Grant.PERSONAL in grants:
        if principal.person_id is None:
            # A principal with no Person captured nothing, so `PERSONAL` reaches nothing. Said
            # explicitly rather than left to a comparison against NULL, which would be true of no
            # row and is the right answer arrived at by accident.
            return Event.id.is_(None)
        return Event.captured_by_person_id == principal.person_id
    # Everything else — an explicit denial, or a grant this function does not know how to render.
    # Refusing an unknown grant is the direction that cannot leak: a grant nobody taught this
    # function about must narrow to nothing rather than to everything.
    return Event.id.is_(None)


def readable_events(principal: Principal) -> Select[tuple[Event]]:
    """Every Event this principal may read, and nothing else."""
    statement = select(Event).where(
        Event.org_id == principal.org_id, Event.deleted_at.is_(None)
    )
    for predicate in (_reach_predicate(principal), _sensitivity_predicate(principal)):
        if predicate is not None:
            statement = statement.where(predicate)
    return statement


def get_event(session: Session, principal: Principal, event_id: uuid.UUID) -> Event | None:
    """One Event, or None if it does not exist *or* may not be read.

    Both answers are the same answer on purpose. A restricted Event that 403s tells the caller it
    exists, which is most of what the restriction was protecting.
    """
    return session.scalars(
        readable_events(principal).where(Event.id == event_id)
    ).one_or_none()


def list_events(
    session: Session,
    principal: Principal,
    *,
    filters: EventFilter | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> EventPage:
    """Newest first, which is the only order a capture feed is ever read in.

    The cursor is `(occurred_at, id)` rather than `created_at`: an Event's place in this list is
    when it happened, and two messages can arrive in one batch with identical insert times.
    """
    statement = readable_events(principal)
    narrowed = filters or EventFilter()

    if narrowed.event_type is not None:
        statement = statement.where(Event.type == narrowed.event_type.value)
    if narrowed.source_system is not None:
        statement = statement.where(Event.source_system == narrowed.source_system)
    if narrowed.occurred_after is not None:
        statement = statement.where(Event.occurred_at >= narrowed.occurred_after)
    if narrowed.occurred_before is not None:
        statement = statement.where(Event.occurred_at <= narrowed.occurred_before)
    if narrowed.participant_person_id is not None:
        statement = statement.where(_participates(narrowed.participant_person_id))

    if cursor is not None:
        statement = statement.where(
            or_(
                Event.occurred_at < cursor.created_at,
                and_(Event.occurred_at == cursor.created_at, Event.id < cursor.id),
            )
        )

    rows = list(
        session.scalars(
            statement.order_by(Event.occurred_at.desc(), Event.id.desc()).limit(limit + 1)
        ).all()
    )
    if len(rows) > limit:
        last = rows[limit - 1]
        return EventPage(
            items=rows[:limit], next_cursor=encode_cursor(last.occurred_at, last.id)
        )
    return EventPage(items=rows, next_cursor=None)


#: Why an Event is related to the one being read. Two reasons, ordered: the stronger first.
THIS_EVENT = "this_event"
SAME_THREAD = "same_thread"


def events_in_thread(
    session: Session, principal: Principal, *, event_id: uuid.UUID
) -> tuple[tuple[uuid.UUID, str], ...]:
    """This Event and the rest of its conversation, each with why it is in the list.

    The Event itself is always first and always present — a message is trivially part of its own
    conversation, and callers that special-cased it kept getting the ordering wrong.

    Reads through `readable_events`, so a sibling the caller may not read contributes nothing. A
    conversation is an efficient way to learn that a restricted message exists, and the defence is
    to never select the row rather than to drop it afterwards.

    Ordering is fixed — this Event, then siblings by id — because what is built on top of it is a
    candidate set the intent validator refuses against, and a set that varied between runs would
    make that refusal unreproducible.
    """
    anchor = session.execute(
        select(Event.source_system, Event.thread_ref).where(
            Event.id == event_id, Event.org_id == principal.org_id
        )
    ).one_or_none()
    if anchor is None:
        return ()
    if anchor.thread_ref is None:
        return ((event_id, THIS_EVENT),)

    readable = readable_events(principal).with_only_columns(Event.id).subquery()
    siblings = session.scalars(
        select(Event.id)
        .where(
            Event.org_id == principal.org_id,
            Event.source_system == anchor.source_system,
            Event.thread_ref == anchor.thread_ref,
            Event.deleted_at.is_(None),
            Event.id != event_id,
            Event.id.in_(select(readable.c.id)),
        )
        .order_by(Event.id)
    ).all()
    return ((event_id, THIS_EVENT), *((sibling, SAME_THREAD) for sibling in siblings))
