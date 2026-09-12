"""Data access for Commitment."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, Select, Update, and_, func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.contexts.commitment.models import Commitment
from app.platform.concurrency import StaleVersionError
from app.platform.ids import uuid7


def get(session: Session, *, org_id: uuid.UUID, commitment_id: uuid.UUID) -> Commitment | None:
    return session.scalars(
        select(Commitment).where(Commitment.org_id == org_id, Commitment.id == commitment_id)
    ).one_or_none()


def insert(
    session: Session,
    *,
    org_id: uuid.UUID,
    statement: str,
    committed_by_person_id: uuid.UUID,
    committed_to_person_id: uuid.UUID | None,
    committed_to_team_id: uuid.UUID | None,
    due_date: dt.date | None,
    due_precision: str,
    status: str,
    fulfilling_work_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    origin_event_id: uuid.UUID | None,
    confidence: int,
    created_by_person_id: uuid.UUID | None,
) -> Commitment:
    commitment = Commitment(
        id=uuid7(),
        org_id=org_id,
        statement=statement,
        committed_by_person_id=committed_by_person_id,
        committed_to_person_id=committed_to_person_id,
        committed_to_team_id=committed_to_team_id,
        due_date=due_date,
        due_precision=due_precision,
        status=status,
        fulfilling_work_id=fulfilling_work_id,
        project_id=project_id,
        origin_event_id=origin_event_id,
        confidence=confidence,
        created_by_person_id=created_by_person_id,
    )
    session.add(commitment)
    session.flush()
    return commitment


def readable(org_id: uuid.UUID) -> Select[tuple[Commitment]]:
    """Every Commitment in the organization.

    No narrowing beyond the tenant. A promise is a fact about who owes what to whom, and the matrix
    gives every role an ORG grant on `COMMITMENT.READ` for the same reason the directory is
    organization-wide: accountability that only its parties can see is not accountability.
    """
    return select(Commitment).where(Commitment.org_id == org_id)


def for_person(org_id: uuid.UUID, person_id: uuid.UUID) -> Select[tuple[Commitment]]:
    """Promises this person made or was made."""
    return readable(org_id).where(
        or_(
            Commitment.committed_by_person_id == person_id,
            Commitment.committed_to_person_id == person_id,
        )
    )


def due_for_missing(
    session: Session, *, org_id: uuid.UUID, today: dt.date
) -> Sequence[Commitment]:
    """BR-C-06's candidates: open, dated, and precise enough to be auto-missed.

    Vague-precision commitments are excluded here rather than filtered later, because the index
    behind this query is partial on exactly that predicate — the sweep and the index agree by
    construction.
    """
    return session.scalars(
        readable(org_id).where(
            and_(
                Commitment.status == "open",
                Commitment.due_date.is_not(None),
                Commitment.due_date < today,
                Commitment.due_precision.in_(("exact", "week")),
            )
        )
    ).all()


def _apply(
    session: Session, stmt: Update, *, entity_id: uuid.UUID, expected: int
) -> None:
    """A version-guarded UPDATE. No rows touched means somebody else got there first."""
    result = cast("CursorResult[Any]", session.execute(stmt))
    if result.rowcount == 0:
        raise StaleVersionError("commitment", entity_id, expected)


def update(
    session: Session,
    *,
    org_id: uuid.UUID,
    commitment_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> Commitment:
    stmt = (
        sa_update(Commitment)
        .where(
            Commitment.org_id == org_id,
            Commitment.id == commitment_id,
            Commitment.version == expected_version,
        )
        .values(**values, version=Commitment.version + 1, updated_at=func.now())
    )
    _apply(session, stmt, entity_id=commitment_id, expected=expected_version)
    session.expire_all()
    updated = get(session, org_id=org_id, commitment_id=commitment_id)
    if updated is None:  # pragma: no cover - the guarded update just succeeded
        raise StaleVersionError("commitment", commitment_id, expected_version)
    return updated
