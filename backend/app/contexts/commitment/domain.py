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
import re
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


# --------------------------------------------------------------------------- due phrases (ADR-0055)

#: How long a deadline phrase may be before it stops being a deadline phrase.
#:
#: "by Friday" is four to ten characters. A model quoting half a sentence has quoted something
#: other than a deadline, and a long quote is where a negation hides — "I will *not* manage this by
#: Friday" contains "by Friday" and means the opposite. Refusing the long quote costs a date the
#: system was not sure of and buys the guarantee that a resolved date came from a short phrase.
MAX_DUE_PHRASE = 40

_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NEXT_WEEKDAY = re.compile(r"\bnext\s+(" + "|".join(_WEEKDAYS) + r")\b")
_WEEKDAY = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b")
_NEXT_MONTH = re.compile(r"\bnext\s+month\b")
_THIS_MONTH = re.compile(r"\b(?:end\s+of\s+(?:the\s+)?month|this\s+month)\b")
_NEXT_WEEK = re.compile(r"\bnext\s+week\b")
_THIS_WEEK = re.compile(r"\b(?:end\s+of\s+(?:the\s+)?week|this\s+week)\b")
_TOMORROW = re.compile(r"\btomorrow\b")
_TODAY = re.compile(r"\btoday\b")

#: A day and a named month — "18 September", "September 18th". Not a form this table reads, and
#: that is exactly why it is matched here.
#:
#: A phrase naming a calendar day names *that* day, and none of the relative rules below can see
#: it. Without this guard "Friday 18 September" falls through to the bare-weekday rule and resolves
#: to the Friday of the week the message was sent: CP24 watched a real message promising the 18th
#: become a commitment due the 11th — a week early, `week` precision, and reported as read
#: successfully. Declining is what this module does everywhere else, because a date nobody agreed
#: to produces a missed-deadline alert about a deadline that was never set.
#:
#: A day number is required rather than a bare month name, so that "may" the modal verb is not
#: mistaken for May the month, and so the guard stays narrow: "by May" was already vague.
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
    r"|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DAY_AND_MONTH = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\b"
    rf"|\b(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\b"
)


@dataclasses.dataclass(frozen=True, slots=True)
class DueReading:
    """What a deadline phrase turned out to mean, if anything.

    `VAGUE` with no date is a complete, valid answer and the default one. It says the promise is
    real and its deadline is not — which is a fact worth recording, and is what BR-C-06 skips
    rather than guesses about.
    """

    date: dt.date | None
    precision: DuePrecision


#: The answer whenever the phrase is absent, too long, unrecognised, or points backwards.
UNREAD_DUE = DueReading(date=None, precision=DuePrecision.VAGUE)


def _end_of_week(day: dt.date) -> dt.date:
    """Sunday of `day`'s ISO week. Weeks start on Monday because ISO-8601 says so."""
    return day + dt.timedelta(days=6 - day.weekday())


def _end_of_month(day: dt.date) -> dt.date:
    first_of_next = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return first_of_next - dt.timedelta(days=1)


def _next_weekday(reference: dt.date, weekday: int, *, skip_a_week: bool) -> dt.date:
    """The named weekday on or after `reference`, or in the week after that one.

    "Friday" said on a Friday means today, not a week today: a promise made in the morning about
    the end of the day is the commonest deadline there is. "Next Friday" is the one after this
    week's, which is the reading that does not quietly bring a deadline forward.
    """
    ahead = (weekday - reference.weekday()) % 7
    if skip_a_week:
        ahead += 7
    return reference + dt.timedelta(days=ahead)


def read_due_phrase(phrase: str | None, *, reference: dt.date) -> DueReading:
    """Turn a quoted deadline phrase into a date, or decline to (ADR-0055, BR-C-05).

    Pure, total and closed. The caller supplies the day the promise was made — never "today" —
    so the same Event resolves to the same date on every run, in every process, forever. A model
    never supplies a date here: it quotes, and this reads the quote.

    Everything outside the small table below returns `UNREAD_DUE`. "Before the meeting" names a
    real deadline that this cannot place, and the honest answer is a `vague` promise with no date
    rather than a date nobody agreed to — a system that invents precision produces a
    missed-deadline alert about a deadline that was never set.

    **Only a calendar date, "today" and "tomorrow" are `exact`.** A named weekday is `week`: "by
    Friday" is a week's promise with a Friday in it, which is the reading the domain has always
    described. Erring toward less claimed precision costs nothing — `week` is still auto-missable
    (BR-C-06) — and overstating it would put a false certainty into an alert.

    A date before the day the promise was made is refused. Far likelier a quotation of something
    the message *mentioned* than a deadline somebody set in the past.
    """
    if phrase is None:
        return UNREAD_DUE
    text = " ".join(phrase.lower().split())
    if not text or len(text) > MAX_DUE_PHRASE:
        return UNREAD_DUE

    reading = _read(text, reference)
    if reading.date is not None and reading.date < reference:
        return UNREAD_DUE
    return reading


def _read(text: str, reference: dt.date) -> DueReading:
    """The table itself. Ordered most specific first: "next Friday" is not "Friday"."""
    iso = _ISO_DATE.search(text)
    if iso is not None:
        try:
            return DueReading(
                date=dt.date(int(iso[1]), int(iso[2]), int(iso[3])),
                precision=DuePrecision.EXACT,
            )
        except ValueError:
            # A well-formed string that is not a day — 2026-02-30. Not a date, so not a deadline.
            return UNREAD_DUE

    if _DAY_AND_MONTH.search(text):
        # After the ISO branch, so `2026-09-18` still reads, and before every relative rule, so a
        # phrase naming a calendar day is never answered by a rule that cannot see it.
        return UNREAD_DUE

    if _TODAY.search(text):
        return DueReading(date=reference, precision=DuePrecision.EXACT)
    if _TOMORROW.search(text):
        return DueReading(
            date=reference + dt.timedelta(days=1), precision=DuePrecision.EXACT
        )

    next_weekday = _NEXT_WEEKDAY.search(text)
    if next_weekday is not None:
        return DueReading(
            date=_next_weekday(reference, _WEEKDAYS[next_weekday[1]], skip_a_week=True),
            precision=DuePrecision.WEEK,
        )

    if _NEXT_MONTH.search(text):
        following = (reference.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return DueReading(date=_end_of_month(following), precision=DuePrecision.MONTH)
    if _THIS_MONTH.search(text):
        return DueReading(date=_end_of_month(reference), precision=DuePrecision.MONTH)

    if _NEXT_WEEK.search(text):
        return DueReading(
            date=_end_of_week(reference) + dt.timedelta(days=7), precision=DuePrecision.WEEK
        )
    if _THIS_WEEK.search(text):
        return DueReading(date=_end_of_week(reference), precision=DuePrecision.WEEK)

    weekday = _WEEKDAY.search(text)
    if weekday is not None:
        return DueReading(
            date=_next_weekday(reference, _WEEKDAYS[weekday[1]], skip_a_week=False),
            precision=DuePrecision.WEEK,
        )

    return UNREAD_DUE
