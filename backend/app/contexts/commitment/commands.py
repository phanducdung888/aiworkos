"""Command objects for the Commitment context."""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

from app.contexts.commitment.domain import CommitmentStatus, DuePrecision


@dataclasses.dataclass(frozen=True, slots=True)
class CreateCommitment:
    statement: str
    committed_by_person_id: uuid.UUID
    committed_to_person_id: uuid.UUID | None = None
    committed_to_team_id: uuid.UUID | None = None
    due_date: dt.date | None = None
    due_precision: DuePrecision = DuePrecision.VAGUE
    fulfilling_work_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    origin_event_id: uuid.UUID | None = None
    confidence: int = 0
    #: BR-C-03. Set by the Tool Gateway when executing an AI-originated Proposal; a person entering
    #: their own promise leaves it false and needs no Evidence.
    produced_by_ai: bool = False
    evidence_ids: tuple[uuid.UUID, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class UpdateCommitment:
    commitment_id: uuid.UUID
    expected_version: int
    statement: str | None = None
    due_date: dt.date | None = None
    due_precision: DuePrecision | None = None
    fulfilling_work_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ChangeCommitmentStatus:
    commitment_id: uuid.UUID
    expected_version: int
    target: CommitmentStatus
    #: BR-C-07. Required when renegotiating and meaningless otherwise.
    new_due_date: dt.date | None = None
    #: BR-C-10. The Tool Gateway sets this when an approved Proposal is what authorised fulfilment.
    approval_record_id: uuid.UUID | None = None
    produced_by_ai: bool = False
