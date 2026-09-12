"""Commitment rules (BR-C-01 to BR-C-10). No I/O.

A Commitment is a promise somebody made, recorded as they made it. It is not an assignment: a
WorkAssignment says the organization decided somebody is responsible for something, while a
Commitment says a person said they would do a thing. The two can describe the same intention and are
different facts about it — which is why `fulfilling_work_id` links them without merging them
(BR-C-08), and why completing the Work *proposes* fulfilment rather than forcing it.

The lifecycle has one shape worth reading twice. `captured` means the promise was heard and nobody
has confirmed it, and `disputed` is a first-class outcome of that confirmation: the named committer
says they did not promise that. A disputed commitment is retained with all its evidence, because a
dispute is itself signal (BR-C-05).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid

from app.platform.errors import DomainRuleViolation


class CommitmentStatus(enum.StrEnum):
    CAPTURED = "captured"
    OPEN = "open"
    FULFILLED = "fulfilled"
    MISSED = "missed"
    RENEGOTIATED = "renegotiated"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"
    WITHDRAWN = "withdrawn"


class DuePrecision(enum.StrEnum):
    """Conversational dates are rarely exact.

    "By Friday" is a week; "end of the month" is a month; "soon" is vague and must never be turned
    into a date, because a system that invents precision produces a missed-deadline alert about a
    deadline nobody set.
    """

    EXACT = "exact"
    WEEK = "week"
    MONTH = "month"
    VAGUE = "vague"


#: BR-C-04, exactly as written. A transition absent from this table does not happen.
TRANSITIONS: dict[CommitmentStatus, frozenset[CommitmentStatus]] = {
    CommitmentStatus.CAPTURED: frozenset(
        {CommitmentStatus.OPEN, CommitmentStatus.DISPUTED, CommitmentStatus.WITHDRAWN}
    ),
    CommitmentStatus.OPEN: frozenset(
        {
            CommitmentStatus.FULFILLED,
            CommitmentStatus.MISSED,
            CommitmentStatus.RENEGOTIATED,
            CommitmentStatus.CANCELLED,
        }
    ),
    CommitmentStatus.RENEGOTIATED: frozenset({CommitmentStatus.OPEN}),
    #: Late completion. A missed promise that is then kept is kept, and the record shows both.
    CommitmentStatus.MISSED: frozenset({CommitmentStatus.FULFILLED}),
    CommitmentStatus.DISPUTED: frozenset(),
    CommitmentStatus.WITHDRAWN: frozenset(),
    CommitmentStatus.FULFILLED: frozenset(),
    CommitmentStatus.CANCELLED: frozenset(),
}

#: BR-C-06. Only these can be auto-missed; a vague promise ages into a review prompt instead.
AUTO_MISSABLE_PRECISION = frozenset({DuePrecision.EXACT, DuePrecision.WEEK})


@dataclasses.dataclass(frozen=True, slots=True)
class Authority:
    """Who the actor is relative to this Commitment (BR-C-09).

    Supplied by the service, which knows the rows. The domain states the rule; it does not go
    looking for the facts.
    """

    is_committer: bool = False
    is_recipient: bool = False
    is_lead_in_chain: bool = False
    is_system: bool = False

    @property
    def may_change_status(self) -> bool:
        return (
            self.is_committer
            or self.is_recipient
            or self.is_lead_in_chain
            or self.is_system
        )


def validate_creation(
    *,
    statement: str,
    committed_to_person_id: uuid.UUID | None,
    committed_to_team_id: uuid.UUID | None,
    due_date: dt.date | None,
    due_precision: DuePrecision,
    confidence: int,
    has_evidence: bool,
    produced_by_ai: bool,
) -> None:
    # BR-C-01. The statement is the promise in the committer's own words where possible, so an
    # empty one leaves a row asserting that somebody promised something unspecified.
    if not statement.strip():
        raise DomainRuleViolation("BR-C-01", "a commitment requires a statement")

    # BR-C-02. Both may be null — a promise made to the room is still a promise — but a promise
    # made to a person *and* a team names two different recipients for one obligation.
    if committed_to_person_id is not None and committed_to_team_id is not None:
        raise DomainRuleViolation(
            "BR-C-02", "a commitment is made to a person or a team, not both"
        )

    if not 0 <= confidence <= 100:
        raise DomainRuleViolation("BR-C-01", "confidence is a percentage")

    # A precision describes a date. Claiming `exact` with no date is a claim about nothing, and the
    # BR-C-06 sweep would have to guess what it meant.
    if due_date is None and due_precision is not DuePrecision.VAGUE:
        raise DomainRuleViolation(
            "BR-C-01", "a due precision other than vague requires a due date"
        )

    # BR-C-03. A human writing down their own promise is the evidence. An AI asserting somebody
    # made one must be able to show where it was said.
    if produced_by_ai and not has_evidence:
        raise DomainRuleViolation(
            "BR-C-03", "an AI-sourced commitment requires at least one evidence row"
        )


def validate_transition(
    current: CommitmentStatus, target: CommitmentStatus, *, authority: Authority
) -> None:
    """BR-C-04 and BR-C-09 together: is this a real transition, and may this person make it."""
    if current == target:
        raise DomainRuleViolation("BR-C-04", f"the commitment is already {target.value}")
    allowed = TRANSITIONS[current]
    if target not in allowed:
        permitted = ", ".join(sorted(s.value for s in allowed)) or "nothing"
        raise DomainRuleViolation(
            "BR-C-04", f"{current.value} may become {permitted}, not {target.value}"
        )
    if not authority.may_change_status:
        raise DomainRuleViolation(
            "BR-C-09",
            "only the committer, the recipient or a lead in their chain may change a "
            "commitment's status",
        )


def validate_renegotiation(
    *, current: CommitmentStatus, new_due_date: dt.date | None, previous_due_date: dt.date | None
) -> None:
    """BR-C-07. A renegotiation that does not move the date is not a renegotiation."""
    if current is not CommitmentStatus.OPEN:
        raise DomainRuleViolation("BR-C-04", "only an open commitment can be renegotiated")
    if new_due_date is None:
        raise DomainRuleViolation("BR-C-07", "renegotiation requires a new due date")
    if previous_due_date is not None and new_due_date == previous_due_date:
        raise DomainRuleViolation("BR-C-07", "the new due date is the existing one")


def is_missed(
    *,
    status: CommitmentStatus,
    due_date: dt.date | None,
    due_precision: DuePrecision,
    today: dt.date,
) -> bool:
    """BR-C-06, stated once for the sweep and for any read model that wants to show it.

    Deliberately a pure predicate rather than a status the system writes eagerly: `missed` is a real
    transition with an audit entry, and computing it here does not make it true. A caller that wants
    the state changed performs the transition; a caller that wants to display it asks this.
    """
    if status is not CommitmentStatus.OPEN or due_date is None:
        return False
    if due_precision not in AUTO_MISSABLE_PRECISION:
        return False
    return due_date < today


def assert_fulfilment_is_approved(*, produced_by_ai: bool, has_approval: bool) -> None:
    """BR-C-10, BR-AI-18. AI never marks a promise kept on its own authority.

    Fulfilment is the one transition where being wrong is silently expensive: it closes the loop,
    stops the reminders, and tells everybody the thing happened.
    """
    if produced_by_ai and not has_approval:
        raise DomainRuleViolation(
            "BR-C-10", "marking a commitment fulfilled requires explicit human approval"
        )
