"""Identity domain rules, exercised without a database.

BR-I-01 to BR-I-07. The database enforces several of these too — the department tree trigger, the
partial unique indexes, the check constraints — and that duplication is the point: the constraint is
the backstop, and this is where the reason lives and where a caller gets an error naming the rule
they broke.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.contexts.identity.domain import (
    MAX_DEPARTMENT_DEPTH,
    MIN_ATTRIBUTION_CONFIDENCE,
    ExistingExternalIdentity,
    ExistingRoleAssignment,
    ExistingTeamMembership,
    MembershipStatus,
    PersonStatus,
    ScopeType,
    assert_assignable,
    may_attribute,
    validate_department_placement,
    validate_external_identity,
    validate_membership_end,
    validate_membership_transition,
    validate_organization_membership,
    validate_person_creation,
    validate_person_transition,
    validate_role_assignment,
    validate_team_membership,
)
from app.platform.errors import DomainRuleViolation

NOW = dt.datetime(2026, 9, 12, tzinfo=dt.UTC)


def ids(count: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(count)]


# --------------------------------------------------------------------------- BR-I-01


class TestDepartmentHierarchy:
    def test_a_root_department_needs_no_parent(self) -> None:
        validate_department_placement(department_id=None, parent_id=None, ancestors=[])

    def test_a_department_cannot_be_its_own_parent(self) -> None:
        same = uuid.uuid4()
        with pytest.raises(DomainRuleViolation, match="BR-I-01"):
            validate_department_placement(
                department_id=same, parent_id=same, ancestors=[]
            )

    def test_a_cycle_is_refused(self) -> None:
        """The chain coming back round is what makes a rollup query non-terminating."""
        target, middle = ids(2)
        with pytest.raises(DomainRuleViolation, match="cycle"):
            validate_department_placement(
                department_id=target, parent_id=middle, ancestors=[target]
            )

    def test_the_depth_limit_is_counted_not_assumed(self) -> None:
        # Four ancestors, plus the parent, plus this one is six — one past the limit.
        with pytest.raises(DomainRuleViolation, match="maximum is 5"):
            validate_department_placement(
                department_id=uuid.uuid4(),
                parent_id=uuid.uuid4(),
                ancestors=ids(MAX_DEPARTMENT_DEPTH - 1),
            )

    def test_a_tree_exactly_at_the_limit_is_allowed(self) -> None:
        validate_department_placement(
            department_id=uuid.uuid4(),
            parent_id=uuid.uuid4(),
            ancestors=ids(MAX_DEPARTMENT_DEPTH - 2),
        )


# --------------------------------------------------------------------------- BR-I-02, BR-I-03


class TestTeamMembership:
    def test_a_person_may_belong_to_many_teams(self) -> None:
        """BR-I-02 limits a team to one department, never a person to one team."""
        person = uuid.uuid4()
        # No rule is consulted about other teams: the existing rows here are this team's.
        validate_team_membership(
            person_id=person, person_status=PersonStatus.ACTIVE, existing=[]
        )

    def test_a_second_open_membership_of_the_same_team_is_refused(self) -> None:
        person = uuid.uuid4()
        with pytest.raises(DomainRuleViolation, match="BR-I-03"):
            validate_team_membership(
                person_id=person,
                person_status=PersonStatus.ACTIVE,
                existing=[ExistingTeamMembership(person_id=person, valid_from=NOW, valid_to=None)],
            )

    def test_rejoining_after_leaving_is_allowed(self) -> None:
        """History is retained, so a closed row must not block a new one."""
        person = uuid.uuid4()
        validate_team_membership(
            person_id=person,
            person_status=PersonStatus.ACTIVE,
            existing=[
                ExistingTeamMembership(person_id=person, valid_from=NOW, valid_to=NOW)
            ],
        )

    def test_a_departed_person_cannot_join_a_team(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-05"):
            validate_team_membership(
                person_id=uuid.uuid4(), person_status=PersonStatus.DEPARTED, existing=[]
            )

    def test_a_membership_cannot_end_before_it_began(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-03"):
            validate_membership_end(NOW, NOW - dt.timedelta(days=1))


# --------------------------------------------------------------------------- BR-I-04, BR-I-05


class TestPerson:
    def test_a_person_needs_a_name(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-04"):
            validate_person_creation(display_name="   ", email=None)

    def test_a_person_without_a_sign_in_subject_is_valid(self) -> None:
        """BR-I-04. They cannot authenticate and can still be named as an owner or a committer."""
        assert validate_person_creation(display_name="Mai", email=None) is PersonStatus.ACTIVE

    def test_an_email_that_is_not_one_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-04"):
            validate_person_creation(display_name="Mai", email="not-an-address")

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (PersonStatus.ACTIVE, PersonStatus.INACTIVE),
            (PersonStatus.ACTIVE, PersonStatus.DEPARTED),
            (PersonStatus.INACTIVE, PersonStatus.ACTIVE),
            (PersonStatus.INACTIVE, PersonStatus.DEPARTED),
        ],
    )
    def test_declared_transitions_are_allowed(
        self, current: PersonStatus, target: PersonStatus
    ) -> None:
        validate_person_transition(current, target)

    def test_departure_is_not_undone_by_flipping_a_flag(self) -> None:
        """Somebody returning is a decision made again, with an audit entry of its own."""
        with pytest.raises(DomainRuleViolation, match="BR-I-05"):
            validate_person_transition(PersonStatus.DEPARTED, PersonStatus.ACTIVE)

    def test_a_no_op_transition_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="already"):
            validate_person_transition(PersonStatus.ACTIVE, PersonStatus.ACTIVE)

    @pytest.mark.parametrize(
        "status", [PersonStatus.INACTIVE, PersonStatus.DEPARTED]
    )
    def test_only_an_active_person_takes_new_work(self, status: PersonStatus) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-05"):
            assert_assignable(status)


class TestOrganizationMembership:
    def test_a_second_membership_is_refused_with_an_explanation(self) -> None:
        """The schema enforces uniqueness; this says what to do instead."""
        with pytest.raises(DomainRuleViolation, match="change its status"):
            validate_organization_membership(
                person_status=PersonStatus.ACTIVE, existing_status=MembershipStatus.ENDED
            )

    def test_a_first_membership_is_allowed(self) -> None:
        validate_organization_membership(
            person_status=PersonStatus.ACTIVE, existing_status=None
        )

    def test_a_departed_person_is_not_added(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-05"):
            validate_organization_membership(
                person_status=PersonStatus.DEPARTED, existing_status=None
            )

    def test_an_ended_membership_can_be_reactivated(self) -> None:
        validate_membership_transition(MembershipStatus.ENDED, MembershipStatus.ACTIVE)

    def test_an_undeclared_membership_transition_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-04"):
            validate_membership_transition(
                MembershipStatus.ENDED, MembershipStatus.SUSPENDED
            )


# --------------------------------------------------------------------------- role assignment


class TestRoleAssignment:
    def test_an_organization_scoped_role_names_no_scope(self) -> None:
        validate_role_assignment(
            role="member",
            scope_type=ScopeType.ORGANIZATION,
            scope_id=None,
            person_status=PersonStatus.ACTIVE,
            existing=[],
        )

    def test_a_narrower_role_must_say_which_one(self) -> None:
        """A department_lead with no department is an unbounded grant wearing a narrow name."""
        with pytest.raises(DomainRuleViolation, match="must name the"):
            validate_role_assignment(
                role="department_lead",
                scope_type=ScopeType.DEPARTMENT,
                scope_id=None,
                person_status=PersonStatus.ACTIVE,
                existing=[],
            )

    def test_an_organization_role_may_not_carry_a_scope(self) -> None:
        with pytest.raises(DomainRuleViolation, match="whole organization"):
            validate_role_assignment(
                role="member",
                scope_type=ScopeType.ORGANIZATION,
                scope_id=uuid.uuid4(),
                person_status=PersonStatus.ACTIVE,
                existing=[],
            )

    def test_the_same_role_in_the_same_scope_twice_is_refused(self) -> None:
        team = uuid.uuid4()
        with pytest.raises(DomainRuleViolation, match="already holds"):
            validate_role_assignment(
                role="team_lead",
                scope_type=ScopeType.TEAM,
                scope_id=team,
                person_status=PersonStatus.ACTIVE,
                existing=[
                    ExistingRoleAssignment(
                        role="team_lead",
                        scope_type=ScopeType.TEAM,
                        scope_id=team,
                        revoked_at=None,
                    )
                ],
            )

    def test_the_same_role_in_a_different_scope_is_a_different_grant(self) -> None:
        validate_role_assignment(
            role="team_lead",
            scope_type=ScopeType.TEAM,
            scope_id=uuid.uuid4(),
            person_status=PersonStatus.ACTIVE,
            existing=[
                ExistingRoleAssignment(
                    role="team_lead",
                    scope_type=ScopeType.TEAM,
                    scope_id=uuid.uuid4(),
                    revoked_at=None,
                )
            ],
        )

    def test_a_revoked_grant_does_not_block_a_new_one(self) -> None:
        team = uuid.uuid4()
        validate_role_assignment(
            role="team_lead",
            scope_type=ScopeType.TEAM,
            scope_id=team,
            person_status=PersonStatus.ACTIVE,
            existing=[
                ExistingRoleAssignment(
                    role="team_lead", scope_type=ScopeType.TEAM, scope_id=team, revoked_at=NOW
                )
            ],
        )

    def test_a_departed_person_is_granted_nothing_new(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-05"):
            validate_role_assignment(
                role="member",
                scope_type=ScopeType.ORGANIZATION,
                scope_id=None,
                person_status=PersonStatus.DEPARTED,
                existing=[],
            )


# --------------------------------------------------------------------------- BR-I-06, BR-I-07


class TestExternalIdentity:
    def test_one_handle_maps_to_at_most_one_person(self) -> None:
        """BR-I-07. Two rows claiming the same number is an attribution that cannot be made."""
        with pytest.raises(DomainRuleViolation, match="BR-I-07"):
            validate_external_identity(
                source_system="whatsapp",
                external_id="+84900000001",
                confidence=0,
                existing=[
                    ExistingExternalIdentity(
                        source_system="whatsapp",
                        external_id="+84900000001",
                        person_id=uuid.uuid4(),
                    )
                ],
            )

    def test_the_same_handle_on_a_different_source_system_is_a_different_mapping(self) -> None:
        validate_external_identity(
            source_system="slack",
            external_id="+84900000001",
            confidence=0,
            existing=[
                ExistingExternalIdentity(
                    source_system="whatsapp",
                    external_id="+84900000001",
                    person_id=uuid.uuid4(),
                )
            ],
        )

    @pytest.mark.parametrize("confidence", [-1, 101])
    def test_confidence_is_a_percentage(self, confidence: int) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-06"):
            validate_external_identity(
                source_system="whatsapp",
                external_id="+84900000002",
                confidence=confidence,
                existing=[],
            )

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_both_halves_of_the_mapping_are_required(self, blank: str) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-I-07"):
            validate_external_identity(
                source_system=blank, external_id="x", confidence=0, existing=[]
            )
        with pytest.raises(DomainRuleViolation, match="BR-I-07"):
            validate_external_identity(
                source_system="whatsapp", external_id=blank, confidence=0, existing=[]
            )

    def test_an_unconfirmed_mapping_may_suggest_and_not_attribute(self) -> None:
        """BR-I-06: both halves are required, and confidence alone is not enough."""
        assert may_attribute(confidence=100, confirmed_at=None) is False

    def test_a_confirmed_but_weak_mapping_may_not_attribute(self) -> None:
        assert (
            may_attribute(confidence=MIN_ATTRIBUTION_CONFIDENCE - 1, confirmed_at=NOW) is False
        )

    def test_a_confirmed_and_strong_mapping_may_attribute(self) -> None:
        assert may_attribute(confidence=MIN_ATTRIBUTION_CONFIDENCE, confirmed_at=NOW) is True
