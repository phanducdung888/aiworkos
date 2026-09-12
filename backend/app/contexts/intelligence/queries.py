"""Read paths for Proposal and ApprovalRecord.

Reading is organization-wide. A Proposal is a pending change to shared state and an ApprovalRecord
is the record of who authorised one — both are the kind of thing an organization should be able to
look at, and BR-PR-08 requires the whole chain to be traversable in both directions, which a
narrowed read would break at whichever hop the reader lacked.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.contexts.intelligence import repository
from app.contexts.intelligence.domain import ProposalKind, ProposalStatus
from app.contexts.intelligence.models import ApprovalRecord, Proposal, ProposedChange
from app.platform.authz import Principal
from app.platform.http.pagination import Cursor, encode_cursor


@dataclasses.dataclass(frozen=True, slots=True)
class ProposalFilter:
    status: ProposalStatus | None = None
    kind: ProposalKind | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    routed_to_person_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ProposalPage:
    items: list[Proposal]
    next_cursor: str | None


def get_proposal(
    session: Session, principal: Principal, proposal_id: uuid.UUID
) -> Proposal | None:
    return session.scalars(
        repository.readable(principal.org_id).where(Proposal.id == proposal_id)
    ).one_or_none()


def list_proposals(
    session: Session,
    principal: Principal,
    *,
    filters: ProposalFilter | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> ProposalPage:
    statement = repository.readable(principal.org_id)
    narrowed = filters or ProposalFilter()

    if narrowed.status is not None:
        statement = statement.where(Proposal.status == narrowed.status.value)
    if narrowed.kind is not None:
        statement = statement.where(Proposal.kind == narrowed.kind.value)
    if narrowed.target_type is not None:
        statement = statement.where(Proposal.target_type == narrowed.target_type)
    if narrowed.target_id is not None:
        statement = statement.where(Proposal.target_id == narrowed.target_id)
    if narrowed.routed_to_person_id is not None:
        statement = statement.where(
            Proposal.routed_to_person_id == narrowed.routed_to_person_id
        )
    if narrowed.source_event_id is not None:
        statement = statement.where(Proposal.source_event_id == narrowed.source_event_id)
    if cursor is not None:
        statement = statement.where(
            or_(
                Proposal.created_at > cursor.created_at,
                and_(Proposal.created_at == cursor.created_at, Proposal.id > cursor.id),
            )
        )

    rows = list(
        session.scalars(
            statement.order_by(Proposal.created_at, Proposal.id).limit(limit + 1)
        ).all()
    )
    if len(rows) > limit:
        last = rows[limit - 1]
        return ProposalPage(
            items=rows[:limit], next_cursor=encode_cursor(last.created_at, last.id)
        )
    return ProposalPage(items=rows, next_cursor=None)


def approval_for(
    session: Session, principal: Principal, proposal_id: uuid.UUID
) -> ApprovalRecord | None:
    return repository.approval_for_proposal(
        session, org_id=principal.org_id, proposal_id=proposal_id
    )


def changes_for_proposal(
    session: Session, principal: Principal, proposal_id: uuid.UUID
) -> Sequence[ProposedChange]:
    return repository.changes_for(
        session, org_id=principal.org_id, proposal_id=proposal_id
    )


def evidence_for_proposal(
    session: Session, principal: Principal, proposal_id: uuid.UUID
) -> list[uuid.UUID]:
    return repository.evidence_ids_for(
        session, org_id=principal.org_id, proposal_id=proposal_id
    )


def proposals_citing_evidence(
    session: Session, principal: Principal, evidence_id: uuid.UUID
) -> list[uuid.UUID]:
    """BR-PR-08 backwards: from a citation to everything proposed because of it."""
    return repository.proposals_citing(
        session, org_id=principal.org_id, evidence_id=evidence_id
    )


def expired_as_of(
    session: Session, principal: Principal, *, now: dt.datetime
) -> Sequence[Proposal]:
    """BR-PR-07's candidates. A question, not a sweep — expiring is a state change with an actor."""
    return session.scalars(
        repository.readable(principal.org_id).where(
            and_(Proposal.status == "pending", Proposal.expires_at <= now)
        )
    ).all()


def approval_for_id(
    session: Session, principal: Principal, approval_id: uuid.UUID
) -> ApprovalRecord | None:
    return repository.get_approval(
        session, org_id=principal.org_id, approval_id=approval_id
    )
