"""Proposal and ApprovalRecord rules (BR-PR-01 to BR-PR-08, BR-AI-16 to BR-AI-19).

A Proposal is a reviewable mutation that has not happened. Everything here exists to keep three
things true:

*What was reviewed is what runs.* The action and its hash are fixed at creation; revising produces a
new Proposal rather than editing this one (ADR-0041). A reviewer's decision therefore attaches to
bytes that cannot move underneath it.

*Approval is not execution.* `Proposal.status` is mutable and an ApprovalRecord is not, which is why
they are separate rows. The status says what the reviewer decided; the record binds their authority
to one exact action and carries the outcome of running it.

*Nothing decides itself.* There is no path from `pending` to executed that does not pass through a
person. The MVP autonomy ceiling is Level 2 — propose, then execute exactly what a human approved —
and Level 3 is not representable in this schema (BR-AI-31), because there is no column for it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid

from app.platform.errors import DomainRuleViolation

#: BR-PR-07. A Proposal nobody reviewed in this window expires; expiry is a negative signal rather
#: than a neutral one, because it means something was raised that nobody thought worth deciding.
PROPOSAL_WINDOW = dt.timedelta(days=14)


class ProposalKind(enum.StrEnum):
    CREATE = "create"
    UPDATE = "update"
    LINK = "link"
    CLOSE = "close"


class ProposalStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    ACCEPTED_WITH_EDITS = "accepted_with_edits"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class Decision(enum.StrEnum):
    APPROVED = "approved"
    APPROVED_WITH_EDITS = "approved_with_edits"
    REJECTED = "rejected"


class ExecutionStatus(enum.StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"
    #: A rejection authorises nothing, so its record has no execution to be pending about.
    NOT_APPLICABLE = "not_applicable"


#: Decided states. A Proposal in one of these is finished and does not move again — a second
#: decision on the same Proposal is a new Proposal (BR-PR-02).
TERMINAL_STATUSES = frozenset(
    {
        ProposalStatus.ACCEPTED,
        ProposalStatus.ACCEPTED_WITH_EDITS,
        ProposalStatus.REJECTED,
        ProposalStatus.EXPIRED,
        ProposalStatus.SUPERSEDED,
    }
)

#: BR-AI-06 and BR-AI-23, stated as vocabulary rather than as a filter.
#:
#: These words describe operations no tool implements and none will without an ADR. The check exists
#: because a Proposal's summary and kind are free text a caller chooses, and "close" is a legitimate
#: kind for a Work item while "delete this person" is not a thing the system should even be able to
#: represent as a pending request.
FORBIDDEN_TARGETS = frozenset(
    {"person", "team", "department", "organization", "role_assignment", "membership"}
)


@dataclasses.dataclass(frozen=True, slots=True)
class ProposalView:
    """What the domain needs to know about a stored Proposal."""

    id: uuid.UUID
    status: ProposalStatus
    action_hash: str
    routed_to_person_id: uuid.UUID
    expires_at: dt.datetime
    raised_by_person_id: uuid.UUID | None


def validate_creation(
    *,
    kind: ProposalKind,
    target_type: str,
    target_id: uuid.UUID | None,
    summary: str,
    confidence: int,
    evidence_count: int,
    raised_by_ai: bool,
) -> None:
    if not summary.strip():
        raise DomainRuleViolation("BR-PR-01", "a proposal requires a summary")
    if not 0 <= confidence <= 100:
        raise DomainRuleViolation("BR-PR-01", "confidence is a percentage")

    # BR-AI-08, BR-AI-23. AI may propose a Person or a Team in principle, but no tool executes one
    # and none is planned for the MVP — so a Proposal naming one could only ever expire.
    if target_type in FORBIDDEN_TARGETS:
        raise DomainRuleViolation(
            "BR-AI-08",
            f"{target_type} is not a proposable target; no tool executes changes to it",
        )

    # A create has nothing to point at yet; an update or a close that points at nothing cannot say
    # what it would change.
    if kind is ProposalKind.CREATE and target_id is not None:
        raise DomainRuleViolation(
            "BR-PR-01", "a create proposal names no existing target"
        )
    if kind is not ProposalKind.CREATE and target_id is None:
        raise DomainRuleViolation(
            "BR-PR-01", f"a {kind.value} proposal must name the entity it changes"
        )

    # BR-AI-02. An AI-originated proposal must be able to show where it got the idea.
    if raised_by_ai and evidence_count == 0:
        raise DomainRuleViolation(
            "BR-AI-02", "an AI-originated proposal requires at least one evidence reference"
        )


def assert_decidable(proposal: ProposalView, *, now: dt.datetime) -> None:
    """A Proposal can be decided once, while it is pending and unexpired."""
    if proposal.status is not ProposalStatus.PENDING:
        raise DomainRuleViolation(
            "BR-PR-02", f"this proposal is already {proposal.status.value}"
        )
    if proposal.expires_at <= now:
        # Expiry is checked here rather than only by a sweep, so a Proposal cannot be approved
        # because nothing happened to run yet.
        raise DomainRuleViolation("BR-PR-07", "this proposal has expired")


def assert_may_decide(
    proposal: ProposalView, *, approver_person_id: uuid.UUID | None, is_org_admin: bool
) -> None:
    """BR-PR-04 routing, enforced.

    An organization administrator may decide anything, which is what administering means. Everybody
    else decides what was routed to them — a Proposal that anyone could approve is not a review, it
    is a queue somebody empties.
    """
    if approver_person_id is None:
        raise DomainRuleViolation(
            "BR-PR-01", "a proposal is decided by a person, not a service account"
        )
    if is_org_admin or proposal.routed_to_person_id == approver_person_id:
        return
    raise DomainRuleViolation(
        "BR-PR-04", "this proposal was routed to somebody else"
    )


def assert_action_matches(*, approved_hash: str, recomputed_hash: str) -> None:
    """BR-AI-18, the check the whole design rests on.

    Two independent things have to agree: the bytes stored as approved, and the digest taken when
    they were approved. A mismatch means the action changed after the human saw it, and the only
    safe response is to refuse — never to execute the newer one, and never to execute the older one
    on the assumption that the digest is stale.
    """
    if approved_hash != recomputed_hash:
        raise DomainRuleViolation(
            "BR-AI-18",
            "the approved action does not match its recorded hash; "
            "a changed action requires a new approval",
        )


def assert_not_executed(status: ExecutionStatus) -> None:
    """Exactly once. A retried execution finds the record already spent."""
    if status is ExecutionStatus.EXECUTED:
        raise DomainRuleViolation(
            "BR-PR-01", "this approval has already been executed"
        )
    if status is ExecutionStatus.NOT_APPLICABLE:
        raise DomainRuleViolation("BR-PR-01", "a rejection authorises no execution")


def status_for(decision: Decision) -> ProposalStatus:
    return {
        Decision.APPROVED: ProposalStatus.ACCEPTED,
        Decision.APPROVED_WITH_EDITS: ProposalStatus.ACCEPTED_WITH_EDITS,
        Decision.REJECTED: ProposalStatus.REJECTED,
    }[decision]


def expiry_from(created_at: dt.datetime) -> dt.datetime:
    return created_at + PROPOSAL_WINDOW
