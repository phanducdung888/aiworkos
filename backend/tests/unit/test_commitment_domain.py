"""Commitment rules without a database (BR-C-01 to BR-C-10)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.contexts.commitment.domain import (
    TRANSITIONS,
    Authority,
    CommitmentStatus,
    DuePrecision,
    assert_fulfilment_is_approved,
    is_missed,
    validate_creation,
    validate_renegotiation,
    validate_transition,
)
from app.platform.errors import DomainRuleViolation

TODAY = dt.date(2026, 9, 12)
ANYONE = Authority(is_committer=True)


def creation(**over: object) -> None:
    kwargs: dict[str, object] = {
        "statement": "I will send the revised quote",
        "committed_to_person_id": None,
        "committed_to_team_id": None,
        "due_date": None,
        "due_precision": DuePrecision.VAGUE,
        "confidence": 0,
        "has_evidence": False,
        "produced_by_ai": False,
    }
    kwargs.update(over)
    validate_creation(**kwargs)  # type: ignore[arg-type]


class TestCreation:
    def test_a_promise_needs_words(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-01"):
            creation(statement="   ")

    def test_a_promise_to_the_room_names_nobody(self) -> None:
        """BR-C-02: both may be null. Somebody said it out loud and meant it generally."""
        creation(committed_to_person_id=None, committed_to_team_id=None)

    def test_a_promise_to_a_person_or_a_team_but_not_both(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-02"):
            creation(
                committed_to_person_id=uuid.uuid4(), committed_to_team_id=uuid.uuid4()
            )

    def test_a_precise_promise_needs_a_date(self) -> None:
        """`exact` with no date is a claim about nothing, and the sweep would have to guess."""
        with pytest.raises(DomainRuleViolation, match="BR-C-01"):
            creation(due_date=None, due_precision=DuePrecision.EXACT)

    def test_a_vague_promise_needs_no_date(self) -> None:
        creation(due_date=None, due_precision=DuePrecision.VAGUE)

    def test_a_human_entering_their_own_promise_needs_no_evidence(self) -> None:
        """BR-C-03. Writing it down is the evidence."""
        creation(produced_by_ai=False, has_evidence=False)

    def test_an_ai_sourced_promise_must_show_where_it_was_said(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-03"):
            creation(produced_by_ai=True, has_evidence=False)

    def test_an_ai_sourced_promise_with_evidence_is_accepted(self) -> None:
        creation(produced_by_ai=True, has_evidence=True)


class TestLifecycle:
    def test_the_transition_table_matches_the_rule(self) -> None:
        """BR-C-04 written out, so a change to the code has to change the rule too."""
        assert TRANSITIONS[CommitmentStatus.CAPTURED] == frozenset(
            {CommitmentStatus.OPEN, CommitmentStatus.DISPUTED, CommitmentStatus.WITHDRAWN}
        )
        assert TRANSITIONS[CommitmentStatus.OPEN] == frozenset(
            {
                CommitmentStatus.FULFILLED,
                CommitmentStatus.MISSED,
                CommitmentStatus.RENEGOTIATED,
                CommitmentStatus.CANCELLED,
            }
        )
        assert TRANSITIONS[CommitmentStatus.RENEGOTIATED] == frozenset({CommitmentStatus.OPEN})
        assert TRANSITIONS[CommitmentStatus.MISSED] == frozenset({CommitmentStatus.FULFILLED})

    def test_acknowledging_opens_a_captured_promise(self) -> None:
        validate_transition(
            CommitmentStatus.CAPTURED, CommitmentStatus.OPEN, authority=ANYONE
        )

    def test_a_missed_promise_can_still_be_kept(self) -> None:
        """Late completion. The record shows both that it was late and that it happened."""
        validate_transition(
            CommitmentStatus.MISSED, CommitmentStatus.FULFILLED, authority=ANYONE
        )

    @pytest.mark.parametrize(
        "terminal",
        [
            CommitmentStatus.FULFILLED,
            CommitmentStatus.CANCELLED,
            CommitmentStatus.WITHDRAWN,
            CommitmentStatus.DISPUTED,
        ],
    )
    def test_terminal_states_do_not_move(self, terminal: CommitmentStatus) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-04"):
            validate_transition(terminal, CommitmentStatus.OPEN, authority=ANYONE)

    def test_a_captured_promise_cannot_jump_straight_to_fulfilled(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-04"):
            validate_transition(
                CommitmentStatus.CAPTURED, CommitmentStatus.FULFILLED, authority=ANYONE
            )

    def test_a_no_op_transition_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="already"):
            validate_transition(
                CommitmentStatus.OPEN, CommitmentStatus.OPEN, authority=ANYONE
            )

    def test_a_disputed_promise_is_retained_not_resolved(self) -> None:
        """BR-C-05. A dispute is itself signal, so there is no transition out of it."""
        assert TRANSITIONS[CommitmentStatus.DISPUTED] == frozenset()


class TestWhoMayChangeIt:
    @pytest.mark.parametrize(
        "authority",
        [
            Authority(is_committer=True),
            Authority(is_recipient=True),
            Authority(is_lead_in_chain=True),
            Authority(is_system=True),
        ],
    )
    def test_the_people_br_c_09_names(self, authority: Authority) -> None:
        validate_transition(
            CommitmentStatus.CAPTURED, CommitmentStatus.OPEN, authority=authority
        )

    def test_a_bystander_may_not(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-09"):
            validate_transition(
                CommitmentStatus.CAPTURED, CommitmentStatus.OPEN, authority=Authority()
            )


class TestRenegotiation:
    def test_renegotiating_requires_a_new_date(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-07"):
            validate_renegotiation(
                current=CommitmentStatus.OPEN, new_due_date=None, previous_due_date=TODAY
            )

    def test_the_new_date_must_differ(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-07"):
            validate_renegotiation(
                current=CommitmentStatus.OPEN, new_due_date=TODAY, previous_due_date=TODAY
            )

    def test_only_an_open_promise_is_renegotiable(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-C-04"):
            validate_renegotiation(
                current=CommitmentStatus.CAPTURED,
                new_due_date=TODAY,
                previous_due_date=None,
            )

    def test_a_moved_date_is_accepted(self) -> None:
        validate_renegotiation(
            current=CommitmentStatus.OPEN,
            new_due_date=TODAY + dt.timedelta(days=7),
            previous_due_date=TODAY,
        )


class TestMissedDetection:
    def test_an_exact_promise_past_its_date_is_missed(self) -> None:
        assert is_missed(
            status=CommitmentStatus.OPEN,
            due_date=TODAY - dt.timedelta(days=1),
            due_precision=DuePrecision.EXACT,
            today=TODAY,
        )

    def test_the_due_date_itself_is_not_yet_missed(self) -> None:
        assert not is_missed(
            status=CommitmentStatus.OPEN,
            due_date=TODAY,
            due_precision=DuePrecision.EXACT,
            today=TODAY,
        )

    @pytest.mark.parametrize(
        "precision", [DuePrecision.MONTH, DuePrecision.VAGUE]
    )
    def test_an_imprecise_promise_is_never_auto_missed(
        self, precision: DuePrecision
    ) -> None:
        """BR-C-06. "Soon" has no deadline to miss; it ages into a review prompt instead."""
        assert not is_missed(
            status=CommitmentStatus.OPEN,
            due_date=TODAY - dt.timedelta(days=90),
            due_precision=precision,
            today=TODAY,
        )

    def test_only_an_open_promise_is_missed(self) -> None:
        assert not is_missed(
            status=CommitmentStatus.FULFILLED,
            due_date=TODAY - dt.timedelta(days=30),
            due_precision=DuePrecision.EXACT,
            today=TODAY,
        )


class TestFulfilment:
    def test_ai_may_not_close_the_loop_on_its_own(self) -> None:
        """BR-C-10. Fulfilment stops the reminders and tells everybody it happened."""
        with pytest.raises(DomainRuleViolation, match="BR-C-10"):
            assert_fulfilment_is_approved(produced_by_ai=True, has_approval=False)

    def test_ai_may_close_it_with_an_approval(self) -> None:
        assert_fulfilment_is_approved(produced_by_ai=True, has_approval=True)

    def test_a_person_needs_no_approval_record(self) -> None:
        assert_fulfilment_is_approved(produced_by_ai=False, has_approval=False)
