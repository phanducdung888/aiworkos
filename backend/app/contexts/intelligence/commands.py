"""Command objects for the Intelligence context."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from app.contexts.intelligence.domain import Decision, ProposalKind


@dataclasses.dataclass(frozen=True, slots=True)
class ProposedFieldChange:
    """One line of what a reviewer reads. Presentation only — never executed (ADR-0042)."""

    field_path: str
    current_value: Any = None
    proposed_value: Any = None


@dataclasses.dataclass(frozen=True, slots=True)
class RaiseProposal:
    kind: ProposalKind
    target_type: str
    summary: str
    tool: str
    tool_version: str
    arguments: dict[str, Any]
    routed_to_person_id: uuid.UUID
    target_id: uuid.UUID | None = None
    reason: str | None = None
    confidence: int = 0
    source_event_id: uuid.UUID | None = None
    evidence_ids: tuple[uuid.UUID, ...] = ()
    changes: tuple[ProposedFieldChange, ...] = ()
    #: BR-AI-02, set by the runtime that created the interaction — never by a caller.
    #:
    #: There is no `raised_by_ai` field here and there is none in the request schema either
    #: (ADR-0043). Whether a Proposal is AI-originated is read from the acting `Actor`, because an
    #: obligation a caller can decline by omitting a field is not an obligation.
    ai_interaction_id: uuid.UUID | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ReviseProposal:
    """BR-PR-02. Produces a *new* Proposal superseding this one.

    Not an edit: revising changes the action, a changed action has a different hash, and a
    different hash cannot match any approval given for the old one (ADR-0041). Expressing
    revision as a new row makes that structural instead of a rule somebody must remember.
    """

    proposal_id: uuid.UUID
    expected_version: int
    summary: str | None = None
    reason: str | None = None
    arguments: dict[str, Any] | None = None
    changes: tuple[ProposedFieldChange, ...] | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class DecideProposal:
    proposal_id: uuid.UUID
    expected_version: int
    decision: Decision
    #: BR-PR-06. Present only for `approved_with_edits`: the arguments as the approver wants them,
    #: which become the approved action and therefore what the hash is taken over.
    edited_arguments: dict[str, Any] | None = None
    #: BR-PR-03. Optional — friction on rejection is a design smell.
    rejection_reason: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ExecuteApproval:
    approval_id: uuid.UUID
