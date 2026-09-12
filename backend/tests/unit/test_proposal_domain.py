"""Proposal, approval and gateway rules without a database.

The gateway tests are mostly assertions about what the registry does *not* contain. That is
deliberate: ADR-0042's claim is that the set of things an approved Proposal can ever do is
enumerable, and a test that only checked the entries present would pass just as happily against a
registry that had grown a `delete_work` nobody noticed.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.contexts.intelligence.action import Action
from app.contexts.intelligence.domain import (
    FORBIDDEN_TARGETS,
    Decision,
    ExecutionStatus,
    ProposalKind,
    ProposalStatus,
    ProposalView,
    assert_action_matches,
    assert_decidable,
    assert_may_decide,
    assert_not_executed,
    expiry_from,
    status_for,
    validate_creation,
)
from app.contexts.intelligence.gateway import REGISTRY, resolve, validate_arguments
from app.platform.errors import DomainRuleViolation

NOW = dt.datetime(2026, 9, 12, 12, 0, tzinfo=dt.UTC)
ROUTED_TO = uuid.uuid4()


def a_view(**over: object) -> ProposalView:
    kwargs: dict[str, object] = {
        "id": uuid.uuid4(),
        "status": ProposalStatus.PENDING,
        "action_hash": "abc",
        "routed_to_person_id": ROUTED_TO,
        "expires_at": NOW + dt.timedelta(days=7),
        "raised_by_person_id": None,
    }
    kwargs.update(over)
    return ProposalView(**kwargs)  # type: ignore[arg-type]


def creation(**over: object) -> None:
    kwargs: dict[str, object] = {
        "kind": ProposalKind.CREATE,
        "target_type": "work",
        "target_id": None,
        "summary": "Create work for the quote revision",
        "confidence": 80,
        "evidence_count": 0,
        "raised_by_ai": False,
    }
    kwargs.update(over)
    validate_creation(**kwargs)  # type: ignore[arg-type]


class TestRaising:
    def test_a_create_names_no_existing_target(self) -> None:
        creation(kind=ProposalKind.CREATE, target_id=None)

    def test_a_create_carrying_a_target_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-PR-01"):
            creation(kind=ProposalKind.CREATE, target_id=uuid.uuid4())

    @pytest.mark.parametrize(
        "kind", [ProposalKind.UPDATE, ProposalKind.LINK, ProposalKind.CLOSE]
    )
    def test_a_change_must_say_what_it_changes(self, kind: ProposalKind) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-PR-01"):
            creation(kind=kind, target_id=None)

    def test_an_ai_proposal_must_cite_evidence(self) -> None:
        """BR-AI-02. It must be able to show where it got the idea."""
        with pytest.raises(DomainRuleViolation, match="BR-AI-02"):
            creation(raised_by_ai=True, evidence_count=0)

    def test_a_person_proposing_their_own_change_needs_none(self) -> None:
        creation(raised_by_ai=False, evidence_count=0)

    @pytest.mark.parametrize("target", sorted(FORBIDDEN_TARGETS))
    def test_identity_and_governance_targets_are_not_proposable(self, target: str) -> None:
        """BR-AI-08, BR-AI-23. No tool executes these, so a Proposal could only ever expire."""
        with pytest.raises(DomainRuleViolation, match="BR-AI-08"):
            creation(target_type=target, kind=ProposalKind.UPDATE, target_id=uuid.uuid4())

    def test_a_proposal_needs_a_summary(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-PR-01"):
            creation(summary="  ")

    def test_the_review_window_is_fourteen_days(self) -> None:
        assert expiry_from(NOW) == NOW + dt.timedelta(days=14)


class TestDeciding:
    def test_a_pending_unexpired_proposal_is_decidable(self) -> None:
        assert_decidable(a_view(), now=NOW)

    @pytest.mark.parametrize(
        "status",
        [
            ProposalStatus.ACCEPTED,
            ProposalStatus.REJECTED,
            ProposalStatus.SUPERSEDED,
            ProposalStatus.EXPIRED,
        ],
    )
    def test_a_decided_proposal_is_not_decided_again(
        self, status: ProposalStatus
    ) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-PR-02"):
            assert_decidable(a_view(status=status), now=NOW)

    def test_an_expired_proposal_cannot_be_approved(self) -> None:
        """Checked here, not only by a sweep — otherwise a Proposal is approvable because nothing
        has run to expire it yet."""
        with pytest.raises(DomainRuleViolation, match="BR-PR-07"):
            assert_decidable(a_view(expires_at=NOW - dt.timedelta(seconds=1)), now=NOW)

    def test_the_routed_person_may_decide(self) -> None:
        assert_may_decide(a_view(), approver_person_id=ROUTED_TO, is_org_admin=False)

    def test_an_admin_may_decide_anything(self) -> None:
        assert_may_decide(a_view(), approver_person_id=uuid.uuid4(), is_org_admin=True)

    def test_somebody_else_may_not(self) -> None:
        """BR-PR-04. A proposal anyone can approve is a queue, not a review."""
        with pytest.raises(DomainRuleViolation, match="BR-PR-04"):
            assert_may_decide(
                a_view(), approver_person_id=uuid.uuid4(), is_org_admin=False
            )

    def test_a_service_account_may_not_decide(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-PR-01"):
            assert_may_decide(a_view(), approver_person_id=None, is_org_admin=True)

    @pytest.mark.parametrize(
        ("decision", "expected"),
        [
            (Decision.APPROVED, ProposalStatus.ACCEPTED),
            (Decision.APPROVED_WITH_EDITS, ProposalStatus.ACCEPTED_WITH_EDITS),
            (Decision.REJECTED, ProposalStatus.REJECTED),
        ],
    )
    def test_each_decision_has_one_status(
        self, decision: Decision, expected: ProposalStatus
    ) -> None:
        assert status_for(decision) is expected


class TestActionBinding:
    def test_a_matching_hash_is_accepted(self) -> None:
        assert_action_matches(approved_hash="abc", recomputed_hash="abc")

    def test_a_mismatch_is_refused(self) -> None:
        """BR-AI-18. The action changed after the human saw it; neither version may run."""
        with pytest.raises(DomainRuleViolation, match="BR-AI-18"):
            assert_action_matches(approved_hash="abc", recomputed_hash="def")

    def test_an_executed_approval_is_spent(self) -> None:
        with pytest.raises(DomainRuleViolation, match="already been executed"):
            assert_not_executed(ExecutionStatus.EXECUTED)

    def test_a_rejection_authorises_nothing(self) -> None:
        with pytest.raises(DomainRuleViolation, match="rejection"):
            assert_not_executed(ExecutionStatus.NOT_APPLICABLE)

    def test_a_failed_execution_may_be_retried(self) -> None:
        assert_not_executed(ExecutionStatus.FAILED)


class TestTheRegistry:
    def test_an_unknown_tool_is_a_refusal_not_a_fallback(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-AI-16"):
            resolve(Action(tool="do_whatever", tool_version="v1", arguments={}))

    def test_an_unknown_version_is_equally_unknown(self) -> None:
        """An action approved against v1 must not execute against a v2 that means something else."""
        with pytest.raises(DomainRuleViolation, match="BR-AI-16"):
            resolve(Action(tool="create_work", tool_version="v99", arguments={}))

    def test_the_registry_contains_no_forbidden_operation(self) -> None:
        """ADR-0042. Deletion, membership, roles and outbound messaging have no entry at all.

        Absent rather than disabled: there is no flag to get wrong and nothing to switch on by
        accident (BR-AI-06, BR-AI-23, BR-AI-24).
        """
        forbidden = ("delete", "remove", "purge", "grant", "revoke", "member", "role", "send",
                     "email", "message", "notify", "sql", "execute_raw")
        for (name, _), tool in REGISTRY.items():
            for word in forbidden:
                assert word not in name, f"{name} looks like a forbidden operation"
                assert word not in tool.run.__name__, f"{tool.run.__name__} is suspicious"

    def test_every_tool_targets_a_work_core_or_commitment_entity(self) -> None:
        allowed = {"work", "work_assignment", "commitment", "project", "milestone", "dependency"}
        for tool in REGISTRY.values():
            assert tool.target_type in allowed, f"{tool.name} targets {tool.target_type}"

    def test_the_registry_is_small_enough_to_read(self) -> None:
        """The point of ADR-0042 is that the whole surface fits on a screen.

        A registry that has quietly grown past that is one nobody audits, which is the state the
        decision exists to prevent. Raising this number is a deliberate act.
        """
        assert len(REGISTRY) <= 12, "the tool registry has grown; is each entry still justified?"

    def test_missing_required_arguments_are_refused(self) -> None:
        tool = resolve(Action(tool="create_work", tool_version="v1", arguments={}))
        with pytest.raises(DomainRuleViolation, match="missing title"):
            validate_arguments(tool, {})

    def test_unknown_arguments_are_refused_not_ignored(self) -> None:
        """An argument the executor silently drops is a difference between what a reviewer read
        and what would run — the class of problem the action hash exists to prevent."""
        tool = resolve(Action(tool="create_work", tool_version="v1", arguments={}))
        with pytest.raises(DomainRuleViolation, match="unknown arguments"):
            validate_arguments(tool, {"title": "x", "assignee": "someone"})

    def test_a_well_formed_argument_set_is_accepted(self) -> None:
        tool = resolve(Action(tool="create_work", tool_version="v1", arguments={}))
        validate_arguments(tool, {"title": "x", "description": "y"})
