"""Application services for Commitment.

Same sequence as every other context: authorize, validate, persist, audit, emit — in one
transaction, with nothing published from inside the request (ADR-0004).

References are validated through published interfaces and never by reading another context's tables
(ADR-0035): Identity says whether a Person exists, Work Core says whether the fulfilling Work does,
Signal says whether the origin Event does. A bad reference is a `BR-G-01` naming the field, not a
composite foreign key violation arriving from psycopg several layers later.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

import app.contexts.identity.public as identity
import app.contexts.signal.public as signal
import app.contexts.work.public as work
from app.contexts.commitment import authorization, repository
from app.contexts.commitment.commands import (
    ChangeCommitmentStatus,
    CreateCommitment,
    UpdateCommitment,
)
from app.contexts.commitment.domain import (
    CommitmentStatus,
    DuePrecision,
    assert_fulfilment_is_approved,
    is_missed,
    validate_creation,
    validate_renegotiation,
    validate_transition,
)
from app.contexts.commitment.models import Commitment
from app.platform.actor import Actor
from app.platform.audit import record_audit
from app.platform.authz import Action, Decision, Principal, ResourceType, authorize
from app.platform.concurrency import StaleVersionError
from app.platform.errors import DomainRuleViolation, EntityNotFound
from app.platform.outbox import append_domain_event


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceContext:
    session: Session
    principal: Principal
    actor: Actor
    #: Teams this actor leads, for BR-C-09's management-chain test. Supplied by the caller because
    #: the Commitment context does not own the org chart and must not go looking in Identity's
    #: tables to find it.
    leads_team_ids: frozenset[uuid.UUID] = frozenset()


def _snapshot(commitment: Commitment) -> dict[str, Any]:
    return {
        "id": str(commitment.id),
        "statement": commitment.statement,
        "committed_by_person_id": str(commitment.committed_by_person_id),
        "committed_to_person_id": (
            str(commitment.committed_to_person_id) if commitment.committed_to_person_id else None
        ),
        "committed_to_team_id": (
            str(commitment.committed_to_team_id) if commitment.committed_to_team_id else None
        ),
        "due_date": commitment.due_date.isoformat() if commitment.due_date else None,
        "due_precision": commitment.due_precision,
        "status": commitment.status,
        "fulfilling_work_id": (
            str(commitment.fulfilling_work_id) if commitment.fulfilling_work_id else None
        ),
        "origin_event_id": (
            str(commitment.origin_event_id) if commitment.origin_event_id else None
        ),
        "previous_due_date": (
            commitment.previous_due_date.isoformat() if commitment.previous_due_date else None
        ),
        "confidence": commitment.confidence,
    }


class CommitmentService:
    def __init__(self, context: ServiceContext) -> None:
        self._ctx = context

    @property
    def _session(self) -> Session:
        return self._ctx.session

    @property
    def _org_id(self) -> uuid.UUID:
        return self._ctx.principal.org_id

    # ------------------------------------------------------------------ writes

    def create(self, command: CreateCommitment) -> Commitment:
        decision = self._authorize(Action.CREATE, frozenset())
        validate_creation(
            statement=command.statement,
            committed_to_person_id=command.committed_to_person_id,
            committed_to_team_id=command.committed_to_team_id,
            due_date=command.due_date,
            due_precision=command.due_precision,
            confidence=command.confidence,
            has_evidence=bool(command.evidence_ids),
            produced_by_ai=command.produced_by_ai,
        )
        self._assert_references(command)

        commitment = repository.insert(
            self._session,
            org_id=self._org_id,
            statement=command.statement.strip(),
            committed_by_person_id=command.committed_by_person_id,
            committed_to_person_id=command.committed_to_person_id,
            committed_to_team_id=command.committed_to_team_id,
            due_date=command.due_date,
            due_precision=command.due_precision.value,
            # Always `captured`. A promise starts unconfirmed whoever recorded it, and opening it is
            # an acknowledgement somebody makes rather than a default the system assumes.
            status=CommitmentStatus.CAPTURED.value,
            fulfilling_work_id=command.fulfilling_work_id,
            project_id=command.project_id,
            origin_event_id=command.origin_event_id,
            confidence=command.confidence,
            created_by_person_id=self._ctx.actor.person_id,
        )
        self._audit(
            Action.CREATE, commitment.id, None, _snapshot(commitment), decision
        )
        self._emit("CommitmentCreated", commitment)
        return commitment

    def update(self, command: UpdateCommitment) -> Commitment:
        commitment = self._load_at(command.commitment_id, command.expected_version)
        decision = self._authorize(
            Action.UPDATE,
            authorization.commitment_relations(
                commitment, actor_person_id=self._ctx.actor.person_id
            ),
            commitment.id,
        )
        before = _snapshot(commitment)

        # The due date moves through renegotiation (BR-C-07), never through a quiet edit: a promise
        # whose deadline changed without anybody saying so is the failure this entity exists to
        # prevent.
        if command.due_date is not None and command.due_date != commitment.due_date:
            raise DomainRuleViolation(
                "BR-C-07", "changing a due date is a renegotiation, not an update"
            )

        changes: dict[str, Any] = {}
        if command.statement is not None:
            if not command.statement.strip():
                raise DomainRuleViolation("BR-C-01", "a commitment requires a statement")
            changes["statement"] = command.statement.strip()
        if command.due_precision is not None:
            changes["due_precision"] = command.due_precision.value
        if command.fulfilling_work_id is not None:
            work.assert_work_exists(
                self._session,
                org_id=self._org_id,
                work_id=command.fulfilling_work_id,
                field="fulfilling_work_id",
            )
            changes["fulfilling_work_id"] = command.fulfilling_work_id
        if command.project_id is not None:
            changes["project_id"] = command.project_id

        updated = repository.update(
            self._session,
            org_id=self._org_id,
            commitment_id=commitment.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(Action.UPDATE, updated.id, before, _snapshot(updated), decision)
        self._emit("CommitmentUpdated", updated)
        return updated

    def change_status(self, command: ChangeCommitmentStatus) -> Commitment:
        commitment = self._load_at(command.commitment_id, command.expected_version)
        decision = self._authorize(
            Action.CHANGE_STATE,
            authorization.commitment_relations(
                commitment, actor_person_id=self._ctx.actor.person_id
            ),
            commitment.id,
        )
        current = CommitmentStatus(commitment.status)
        authority = authorization.authority_of(
            commitment,
            actor_person_id=self._ctx.actor.person_id,
            leads_team_ids=self._ctx.leads_team_ids,
        )
        validate_transition(current, command.target, authority=authority)

        if command.target is CommitmentStatus.FULFILLED:
            assert_fulfilment_is_approved(
                produced_by_ai=command.produced_by_ai,
                has_approval=command.approval_record_id is not None,
            )
        if command.target is CommitmentStatus.RENEGOTIATED:
            validate_renegotiation(
                current=current,
                new_due_date=command.new_due_date,
                previous_due_date=commitment.due_date,
            )

        before = _snapshot(commitment)
        changes: dict[str, Any] = {"status": command.target.value}
        if command.target is CommitmentStatus.OPEN and commitment.acknowledged_at is None:
            changes["acknowledged_at"] = dt.datetime.now(dt.UTC)
        if command.target is CommitmentStatus.RENEGOTIATED:
            # BR-C-07. The prior date is retained on the row and in the audit before/after pair.
            changes["previous_due_date"] = commitment.due_date
            changes["due_date"] = command.new_due_date

        updated = repository.update(
            self._session,
            org_id=self._org_id,
            commitment_id=commitment.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(Action.CHANGE_STATE, updated.id, before, _snapshot(updated), decision)
        self._emit(f"Commitment{command.target.value.title()}", updated)
        return updated

    # ------------------------------------------------------------------ reads

    def overdue_today(self, today: dt.date | None = None) -> list[Commitment]:
        """BR-C-06 as a question, not as a side effect.

        Returns what *would* be missed. Nothing is written: `missed` is a transition with an audit
        entry, and a read that quietly performed it would make a status change appear with no actor.
        """
        when = today or dt.datetime.now(dt.UTC).date()
        return [
            row
            for row in repository.due_for_missing(
                self._session, org_id=self._org_id, today=when
            )
            if is_missed(
                status=CommitmentStatus(row.status),
                due_date=row.due_date,
                due_precision=DuePrecision(row.due_precision),
                today=when,
            )
        ]

    # ------------------------------------------------------------------ internals

    def _assert_references(self, command: CreateCommitment) -> None:
        identity.assert_person_exists(
            self._session,
            org_id=self._org_id,
            person_id=command.committed_by_person_id,
            field="committed_by_person_id",
        )
        if command.committed_to_person_id is not None:
            identity.assert_person_exists(
                self._session,
                org_id=self._org_id,
                person_id=command.committed_to_person_id,
                field="committed_to_person_id",
            )
        if command.committed_to_team_id is not None:
            identity.assert_team_exists(
                self._session,
                org_id=self._org_id,
                team_id=command.committed_to_team_id,
                field="committed_to_team_id",
            )
        if command.fulfilling_work_id is not None:
            work.assert_work_exists(
                self._session,
                org_id=self._org_id,
                work_id=command.fulfilling_work_id,
                field="fulfilling_work_id",
            )
        if command.origin_event_id is not None:
            signal.assert_event_exists(
                self._session,
                org_id=self._org_id,
                event_id=command.origin_event_id,
                field="origin_event_id",
            )

    def _load_at(self, commitment_id: uuid.UUID, expected_version: int) -> Commitment:
        """Load, and refuse immediately if the caller's view is stale.

        Checked before the domain rules rather than only by the guarded UPDATE at the end. A caller
        holding version 1 of a commitment that has since been acknowledged is not making an invalid
        transition — they are reasoning about a state that no longer exists, and "captured may not
        become fulfilled" would be a true sentence about the wrong row. The guarded write stays as
        the backstop for the race that opens between this check and it.
        """
        commitment = self._load(commitment_id)
        if commitment.version != expected_version:
            raise StaleVersionError("commitment", commitment_id, expected_version)
        return commitment

    def _load(self, commitment_id: uuid.UUID) -> Commitment:
        commitment = repository.get(
            self._session, org_id=self._org_id, commitment_id=commitment_id
        )
        if commitment is None:
            raise EntityNotFound("commitment", commitment_id)
        return commitment

    def _authorize(
        self, action: Action, relations: frozenset[Any], resource_id: uuid.UUID | None = None
    ) -> Decision:
        return authorize(
            self._ctx.principal,
            action,
            authorization.ref(
                ResourceType.COMMITMENT, self._org_id, relations, resource_id
            ),
        )

    def _audit(
        self,
        action: Action,
        resource_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        decision: Decision,
    ) -> None:
        record_audit(
            self._session,
            org_id=self._org_id,
            actor=self._ctx.actor,
            action=action,
            resource_type=ResourceType.COMMITMENT,
            resource_id=resource_id,
            before=before,
            after=after,
            decision=decision,
        )

    def _emit(self, event_type: str, commitment: Commitment) -> None:
        append_domain_event(
            self._session,
            org_id=self._org_id,
            type=event_type,
            aggregate_type="commitment",
            aggregate_id=commitment.id,
            payload={
                "commitment_id": str(commitment.id),
                "status": commitment.status,
                "committed_by_person_id": str(commitment.committed_by_person_id),
                "due_date": commitment.due_date.isoformat() if commitment.due_date else None,
            },
            actor=self._ctx.actor,
        )
