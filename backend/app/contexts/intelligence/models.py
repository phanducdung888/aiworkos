"""Proposal, ProposedChange, ProposalEvidence and ApprovalRecord. Mirrors migration 0010."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base

_TS = TIMESTAMP(timezone=True)

#: A nullable JSONB column, meaning SQL NULL rather than the JSON value `null`.
#:
#: SQLAlchemy renders a Python `None` into a JSON/JSONB column as JSON `null` by default, not as
#: SQL NULL. A check constraint written as `edits IS NULL` is then false for a row that looks empty
#: in every query, which is exactly the kind of disagreement between the schema and the ORM that is
#: invisible until a constraint fires. `none_as_null` makes the two mean the same thing.
_NULLABLE_JSONB = JSONB(none_as_null=True)


class Proposal(Base):
    __tablename__ = "proposal"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="proposal_org_id_unique"),
        ForeignKeyConstraint(
            ["org_id", "source_event_id"], ["event.org_id", "event.id"], name="proposal_event_fk"
        ),
        ForeignKeyConstraint(
            ["org_id", "supersedes_proposal_id"],
            ["proposal.org_id", "proposal.id"],
            name="proposal_supersedes_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "ai_interaction_id"],
            ["ai_interaction.org_id", "ai_interaction.id"],
            name="proposal_ai_interaction_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    #: BR-AI-17. Why this is being proposed, including what is *not* known — a Proposal preserves
    #: uncertainty rather than inventing a value to look complete.
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(SmallInteger, server_default="0", nullable=False)
    #: The fully resolved tool call. Frozen by the immutability trigger (ADR-0041).
    action: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    action_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: BR-PR-02. Set on the replacement when a Proposal is revised or its target moved.
    supersedes_proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    raised_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: BR-PR-04. Every Proposal has a defined recipient; there is no unrouted queue.
    routed_to_person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)
    reviewed_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False)
    #: BR-AI-02. Which run produced this, or null for a human's own proposal.
    ai_interaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


class ProposalEvidence(Base):
    """BR-AI-02, BR-PR-08. A table rather than an array so the chain traverses both ways."""

    __tablename__ = "proposal_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "proposal_id"],
            ["proposal.org_id", "proposal.id"],
            name="proposal_evidence_proposal_fk",
        ),
        ForeignKeyConstraint(
            ["org_id", "evidence_id"],
            ["evidence.org_id", "evidence.id"],
            name="proposal_evidence_evidence_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )


class ProposedChange(Base):
    """What a reviewer reads: field by field, now versus proposed.

    Presentation of the action, never the thing executed. ADR-0042 refuses a generic field-path
    applier, so nothing reads these rows to perform a mutation — if it did, this table would be an
    arbitrary write surface wearing a reviewer-friendly name.
    """

    __tablename__ = "proposed_change"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "proposal_id"],
            ["proposal.org_id", "proposal.id"],
            name="proposed_change_proposal_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    current_value: Mapped[dict[str, object] | None] = mapped_column(_NULLABLE_JSONB)
    proposed_value: Mapped[dict[str, object] | None] = mapped_column(_NULLABLE_JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )


class ApprovalRecord(Base):
    """An immutable binding of a human's authority to one exact action.

    Separate from `Proposal.status` because status is mutable and an approval must not be
    (domain-model §ApprovalRecord). Everything except the execution outcome is frozen by trigger;
    the outcome fields are written once.
    """

    __tablename__ = "approval_record"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="approval_record_org_id_unique"),
        ForeignKeyConstraint(
            ["org_id", "proposal_id"],
            ["proposal.org_id", "proposal.id"],
            name="approval_proposal_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approver_person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    #: The exact action as approved — the edited one when approving with edits (BR-PR-06).
    approved_action: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    approved_action_hash: Mapped[str] = mapped_column(Text, nullable=False)
    edits: Mapped[dict[str, object] | None] = mapped_column(_NULLABLE_JSONB)
    decided_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    execution_status: Mapped[str] = mapped_column(
        Text, server_default="pending", nullable=False
    )
    resulting_entity_type: Mapped[str | None] = mapped_column(Text)
    resulting_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resulting_audit_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    executed_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    execution_error: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


class AIInteraction(Base):
    """The record of one AI run (BR-PR-08, domain-model §AIInteraction).

    Append-mostly: the outcome fields are written when the run ends and nothing else ever changes.
    A provenance link that can be rewritten is not a link, and this row is the hop between an Event
    and everything the run proposed because of it.

    No column here holds a credential, a prompt body or a raw model response. What it holds is
    enough to answer *which run was this, under whose authority, with which model and prompt, and
    what came out* — the questions an auditor asks. Reproducing the run is the prompt registry's
    job, and `prompt_id`/`prompt_version` are the pointer to it.
    """

    __tablename__ = "ai_interaction"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="ai_interaction_org_id_unique"),
        ForeignKeyConstraint(
            ["org_id", "principal_person_id"],
            ["person.org_id", "person.id"],
            name="ai_interaction_principal_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: BR-AI-03. The human whose authority this run borrowed.
    principal_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    agent_identity: Mapped[str] = mapped_column(Text, nullable=False)
    runtime: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: Pinned, never "latest" — a check constraint enforces it. A run that cannot say which prompt
    #: produced it is not reproducible, and BR-AI-32's promotion metrics would measure nothing.
    prompt_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    tool_manifest_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: Identifiers only — which Events and entities were given as context, never their contents.
    input_refs: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, server_default="running", nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, object] | None] = mapped_column(_NULLABLE_JSONB)
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_summary: Mapped[dict[str, object] | None] = mapped_column(_NULLABLE_JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


class ToolCall(Base):
    """Every tool call the runtime made, including the refused ones.

    The refusals are the interesting rows. A denial is the authority model working, and a table
    that kept only successes would make the one thing worth auditing invisible — you would see what
    an agent did and never what it tried.

    Append-only outright: no field on a recorded call should ever change, and a rewritten denial is
    exactly the audit failure this table exists to prevent.
    """

    __tablename__ = "tool_call"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "ai_interaction_id"],
            ["ai_interaction.org_id", "ai_interaction.id"],
            name="tool_call_interaction_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    ai_interaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: The shape of the call — argument names and types, never their values.
    arguments_redacted: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    authorization_result: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    target_entity_type: Mapped[str | None] = mapped_column(Text)
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
