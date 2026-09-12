"""Read paths for Commitment.

No visibility narrowing beyond the tenant, and that is a decision rather than an omission. A
Commitment records who owes what to whom; accountability visible only to its parties is not
accountability, and the matrix gives every role an ORG grant on `COMMITMENT.READ` for the same
reason the people directory is organization-wide.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.contexts.commitment import repository
from app.contexts.commitment.domain import CommitmentStatus
from app.contexts.commitment.models import Commitment
from app.platform.authz import Principal
from app.platform.http.pagination import Cursor, encode_cursor


@dataclasses.dataclass(frozen=True, slots=True)
class CommitmentFilter:
    status: CommitmentStatus | None = None
    committed_by_person_id: uuid.UUID | None = None
    committed_to_person_id: uuid.UUID | None = None
    fulfilling_work_id: uuid.UUID | None = None
    origin_event_id: uuid.UUID | None = None
    due_before: dt.date | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class CommitmentPage:
    items: list[Commitment]
    next_cursor: str | None


def get_commitment(
    session: Session, principal: Principal, commitment_id: uuid.UUID
) -> Commitment | None:
    return session.scalars(
        repository.readable(principal.org_id).where(Commitment.id == commitment_id)
    ).one_or_none()


def list_commitments(
    session: Session,
    principal: Principal,
    *,
    filters: CommitmentFilter | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> CommitmentPage:
    statement = repository.readable(principal.org_id)
    narrowed = filters or CommitmentFilter()

    if narrowed.status is not None:
        statement = statement.where(Commitment.status == narrowed.status.value)
    if narrowed.committed_by_person_id is not None:
        statement = statement.where(
            Commitment.committed_by_person_id == narrowed.committed_by_person_id
        )
    if narrowed.committed_to_person_id is not None:
        statement = statement.where(
            Commitment.committed_to_person_id == narrowed.committed_to_person_id
        )
    if narrowed.fulfilling_work_id is not None:
        statement = statement.where(
            Commitment.fulfilling_work_id == narrowed.fulfilling_work_id
        )
    if narrowed.origin_event_id is not None:
        statement = statement.where(Commitment.origin_event_id == narrowed.origin_event_id)
    if narrowed.due_before is not None:
        statement = statement.where(
            and_(Commitment.due_date.is_not(None), Commitment.due_date < narrowed.due_before)
        )
    if cursor is not None:
        statement = statement.where(
            or_(
                Commitment.created_at > cursor.created_at,
                and_(
                    Commitment.created_at == cursor.created_at, Commitment.id > cursor.id
                ),
            )
        )

    rows = list(
        session.scalars(
            statement.order_by(Commitment.created_at, Commitment.id).limit(limit + 1)
        ).all()
    )
    if len(rows) > limit:
        last = rows[limit - 1]
        return CommitmentPage(
            items=rows[:limit], next_cursor=encode_cursor(last.created_at, last.id)
        )
    return CommitmentPage(items=rows, next_cursor=None)
