"""Data access for Proposal and ApprovalRecord."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, Select, and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.contexts.intelligence.models import (
    ApprovalRecord,
    Proposal,
    ProposalEvidence,
    ProposedChange,
)
from app.platform.concurrency import StaleVersionError
from app.platform.ids import uuid7


def get_proposal(
    session: Session, *, org_id: uuid.UUID, proposal_id: uuid.UUID
) -> Proposal | None:
    return session.scalars(
        select(Proposal).where(Proposal.org_id == org_id, Proposal.id == proposal_id)
    ).one_or_none()


def readable(org_id: uuid.UUID) -> Select[tuple[Proposal]]:
    return select(Proposal).where(Proposal.org_id == org_id)


def insert_proposal(
    session: Session,
    *,
    org_id: uuid.UUID,
    kind: str,
    target_type: str,
    target_id: uuid.UUID | None,
    summary: str,
    reason: str | None,
    confidence: int,
    action: dict[str, Any],
    action_hash: str,
    source_event_id: uuid.UUID | None,
    supersedes_proposal_id: uuid.UUID | None,
    raised_by_person_id: uuid.UUID | None,
    routed_to_person_id: uuid.UUID,
    expires_at: dt.datetime,
    ai_interaction_id: uuid.UUID | None = None,
) -> Proposal:
    proposal = Proposal(
        id=uuid7(),
        org_id=org_id,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        reason=reason,
        confidence=confidence,
        action=action,
        action_hash=action_hash,
        source_event_id=source_event_id,
        supersedes_proposal_id=supersedes_proposal_id,
        raised_by_person_id=raised_by_person_id,
        routed_to_person_id=routed_to_person_id,
        expires_at=expires_at,
        ai_interaction_id=ai_interaction_id,
    )
    session.add(proposal)
    session.flush()
    return proposal


def link_evidence(
    session: Session, *, org_id: uuid.UUID, proposal_id: uuid.UUID, evidence_id: uuid.UUID
) -> None:
    session.add(
        ProposalEvidence(
            id=uuid7(), org_id=org_id, proposal_id=proposal_id, evidence_id=evidence_id
        )
    )
    session.flush()


def evidence_ids_for(
    session: Session, *, org_id: uuid.UUID, proposal_id: uuid.UUID
) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(ProposalEvidence.evidence_id).where(
                ProposalEvidence.org_id == org_id,
                ProposalEvidence.proposal_id == proposal_id,
            )
        ).all()
    )


def proposals_citing(
    session: Session, *, org_id: uuid.UUID, evidence_id: uuid.UUID
) -> list[uuid.UUID]:
    """BR-PR-08's reverse direction: from a citation back to what was proposed because of it."""
    return list(
        session.scalars(
            select(ProposalEvidence.proposal_id).where(
                ProposalEvidence.org_id == org_id,
                ProposalEvidence.evidence_id == evidence_id,
            )
        ).all()
    )


def add_change(
    session: Session,
    *,
    org_id: uuid.UUID,
    proposal_id: uuid.UUID,
    field_path: str,
    current_value: Any,
    proposed_value: Any,
) -> None:
    session.add(
        ProposedChange(
            id=uuid7(),
            org_id=org_id,
            proposal_id=proposal_id,
            field_path=field_path,
            current_value={"value": current_value},
            proposed_value={"value": proposed_value},
        )
    )
    session.flush()


def changes_for(
    session: Session, *, org_id: uuid.UUID, proposal_id: uuid.UUID
) -> Sequence[ProposedChange]:
    return session.scalars(
        select(ProposedChange)
        .where(ProposedChange.org_id == org_id, ProposedChange.proposal_id == proposal_id)
        .order_by(ProposedChange.field_path)
    ).all()


def decide_proposal(
    session: Session,
    *,
    org_id: uuid.UUID,
    proposal_id: uuid.UUID,
    expected_version: int,
    status: str,
    reviewed_by_person_id: uuid.UUID | None,
    reviewed_at: dt.datetime | None,
    rejection_reason: str | None = None,
) -> Proposal:
    """A version-guarded decision.

    The guard is what makes "decided once" true under concurrency: two people approving the same
    Proposal in the same second produce one decision and one `StaleVersionError`, rather than two
    ApprovalRecords that the unique index would then reject with a less explicable error.
    """
    stmt = (
        update(Proposal)
        .where(
            Proposal.org_id == org_id,
            Proposal.id == proposal_id,
            Proposal.version == expected_version,
        )
        .values(
            status=status,
            reviewed_by_person_id=reviewed_by_person_id,
            reviewed_at=reviewed_at,
            rejection_reason=rejection_reason,
            version=Proposal.version + 1,
        )
    )
    if cast("CursorResult[Any]", session.execute(stmt)).rowcount == 0:
        raise StaleVersionError("proposal", proposal_id, expected_version)
    session.expire_all()
    decided = get_proposal(session, org_id=org_id, proposal_id=proposal_id)
    if decided is None:  # pragma: no cover - the guarded update just succeeded
        raise StaleVersionError("proposal", proposal_id, expected_version)
    return decided


# --------------------------------------------------------------------------- approval


def insert_approval(
    session: Session,
    *,
    org_id: uuid.UUID,
    proposal_id: uuid.UUID,
    approver_person_id: uuid.UUID,
    decision: str,
    approved_action: dict[str, Any],
    approved_action_hash: str,
    edits: dict[str, Any] | None,
    execution_status: str,
) -> ApprovalRecord:
    record = ApprovalRecord(
        id=uuid7(),
        org_id=org_id,
        proposal_id=proposal_id,
        approver_person_id=approver_person_id,
        decision=decision,
        approved_action=approved_action,
        approved_action_hash=approved_action_hash,
        edits=edits,
        execution_status=execution_status,
    )
    session.add(record)
    session.flush()
    return record


def get_approval(
    session: Session, *, org_id: uuid.UUID, approval_id: uuid.UUID
) -> ApprovalRecord | None:
    return session.scalars(
        select(ApprovalRecord).where(
            ApprovalRecord.org_id == org_id, ApprovalRecord.id == approval_id
        )
    ).one_or_none()


def approval_for_proposal(
    session: Session, *, org_id: uuid.UUID, proposal_id: uuid.UUID
) -> ApprovalRecord | None:
    return session.scalars(
        select(ApprovalRecord).where(
            ApprovalRecord.org_id == org_id, ApprovalRecord.proposal_id == proposal_id
        )
    ).one_or_none()


def claim_for_execution(
    session: Session,
    *,
    org_id: uuid.UUID,
    approval_id: uuid.UUID,
    window: dt.timedelta,
) -> bool:
    """Move an approval from `pending` to executing, exactly once and only in time.

    The conditional UPDATE is the whole mechanism, and it carries **both** invariants: whichever
    caller changes the row wins, and the row only changes while the execution window is open
    (ADR-0051). Checking either in Python and then writing would leave a gap — two executions both
    reading `pending`, or a worker that read an unexpired approval and was descheduled past the
    deadline. There is no arrangement of retries, duplicate deliveries or racing workers that gets
    through this statement after the deadline, because the deadline is part of the statement.

    `decided_at` is frozen by the immutability trigger, so the expression is the same on every
    attempt. Returns True if this caller claimed it.
    """
    stmt = (
        update(ApprovalRecord)
        .where(
            ApprovalRecord.org_id == org_id,
            ApprovalRecord.id == approval_id,
            ApprovalRecord.execution_status == "pending",
            ApprovalRecord.decided_at + window > func.now(),
        )
        .values(version=ApprovalRecord.version + 1)
    )
    return cast("CursorResult[Any]", session.execute(stmt)).rowcount == 1


def record_execution(
    session: Session,
    *,
    org_id: uuid.UUID,
    approval_id: uuid.UUID,
    execution_status: str,
    resulting_entity_type: str | None = None,
    resulting_entity_id: uuid.UUID | None = None,
    resulting_audit_entry_id: uuid.UUID | None = None,
    execution_error: str | None = None,
) -> ApprovalRecord:
    stmt = (
        update(ApprovalRecord)
        .where(ApprovalRecord.org_id == org_id, ApprovalRecord.id == approval_id)
        .values(
            execution_status=execution_status,
            resulting_entity_type=resulting_entity_type,
            resulting_entity_id=resulting_entity_id,
            resulting_audit_entry_id=resulting_audit_entry_id,
            executed_at=dt.datetime.now(dt.UTC) if execution_status == "executed" else None,
            execution_error=execution_error,
            version=ApprovalRecord.version + 1,
        )
    )
    session.execute(stmt)
    session.expire_all()
    updated = get_approval(session, org_id=org_id, approval_id=approval_id)
    if updated is None:  # pragma: no cover
        raise StaleVersionError("approval_record", approval_id, 0)
    return updated


def pending_for_person(
    org_id: uuid.UUID, person_id: uuid.UUID, *, now: dt.datetime
) -> Select[tuple[Proposal]]:
    return readable(org_id).where(
        and_(
            Proposal.status == "pending",
            Proposal.expires_at > now,
            or_(Proposal.routed_to_person_id == person_id),
        )
    )
