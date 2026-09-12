"""Reference predicates for other contexts (ADR-0035).

A Commitment records the Event where the promise was made; a Proposal records the Event its Evidence
came from. Both must be able to refuse an Event that does not resolve without reading Signal's
tables.

Existence, not readability — the same boundary Work Core's predicate draws. A `restricted` Event
that the caller cannot read still exists, and answering otherwise would let a caller probe
sensitivity by watching which references validate.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contexts.signal.models import Event, Evidence
from app.platform.errors import DomainRuleViolation


def assert_event_exists(
    session: Session, *, org_id: uuid.UUID, event_id: uuid.UUID, field: str = "event"
) -> None:
    exists = session.scalars(
        select(Event.id).where(
            Event.org_id == org_id, Event.id == event_id, Event.deleted_at.is_(None)
        )
    ).first()
    if exists is None:
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name an event in this organization"
        )


def assert_evidence_exists(
    session: Session, *, org_id: uuid.UUID, evidence_id: uuid.UUID, field: str = "evidence"
) -> None:
    exists = session.scalars(
        select(Evidence.id).where(Evidence.org_id == org_id, Evidence.id == evidence_id)
    ).first()
    if exists is None:
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name evidence in this organization"
        )
