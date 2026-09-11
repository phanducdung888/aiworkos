"""Work Core domain rules.

Pure tests, no database. The state machines are swept exhaustively rather than sampled, because a
transition table is exactly the kind of thing where the one untested cell is the one that is wrong.
"""

from __future__ import annotations

import datetime as dt
import itertools
import uuid

import pytest

from app.contexts.work.domain import (
    WORK_TRANSITIONS,
    AssignmentRole,
    AssignmentStatus,
    DependencyEdge,
    DependencyKind,
    DependencyStatus,
    DomainRuleViolation,
    Endpoint,
    ExistingAssignment,
    MilestoneStatus,
    ProjectClosureContext,
    ProjectStatus,
    Source,
    WorkPartition,
    WorkStatus,
    WorkTransitionContext,
    completed_at_for,
    count_by_partition,
    current_owner,
    find_cycle,
    partition_of,
    roll_up_project,
    validate_assignment,
    validate_dependency,
    validate_milestone_transition,
    validate_project_creation,
    validate_project_transition,
    validate_work_creation,
    validate_work_hierarchy,
    validate_work_transition,
)

NOW = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.UTC)
ORG = uuid.uuid4()


def work_id() -> uuid.UUID:
    return uuid.uuid4()


def ok_context(**kwargs: object) -> WorkTransitionContext:
    defaults: dict[str, object] = {"blocked_reason": "waiting on vendor"}
    defaults.update(kwargs)
    return WorkTransitionContext(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- work lifecycle


def test_every_transition_pair_is_either_declared_or_refused() -> None:
    for current, target in itertools.product(WorkStatus, WorkStatus):
        if current == target:
            with pytest.raises(DomainRuleViolation):
                validate_work_transition(current, target, ok_context())
            continue
        permitted = target in WORK_TRANSITIONS[current]
        if permitted:
            validate_work_transition(current, target, ok_context())
        else:
            with pytest.raises(DomainRuleViolation) as excinfo:
                validate_work_transition(current, target, ok_context())
            assert excinfo.value.rule == "BR-W-03"


def test_terminal_states_are_terminal() -> None:
    for terminal in (WorkStatus.CANCELLED, WorkStatus.REJECTED):
        assert WORK_TRANSITIONS[terminal] == frozenset()


def test_blocking_requires_a_reason_or_an_active_blocker() -> None:
    bare = WorkTransitionContext()
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_work_transition(WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED, bare)
    assert excinfo.value.rule == "BR-W-04"

    validate_work_transition(
        WorkStatus.IN_PROGRESS,
        WorkStatus.BLOCKED,
        WorkTransitionContext(has_active_blocking_dependency=True),
    )
    validate_work_transition(
        WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED, WorkTransitionContext(blocked_reason="waiting")
    )


def test_a_whitespace_only_reason_is_not_a_reason() -> None:
    with pytest.raises(DomainRuleViolation):
        validate_work_transition(
            WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED, WorkTransitionContext(blocked_reason="   ")
        )


def test_work_cannot_be_completed_while_blocked_or_with_open_children() -> None:
    with pytest.raises(DomainRuleViolation) as blocker:
        validate_work_transition(
            WorkStatus.IN_PROGRESS,
            WorkStatus.DONE,
            WorkTransitionContext(has_active_blocking_dependency=True),
        )
    assert blocker.value.rule == "BR-W-05"

    with pytest.raises(DomainRuleViolation) as children:
        validate_work_transition(
            WorkStatus.IN_PROGRESS, WorkStatus.DONE, WorkTransitionContext(has_open_children=True)
        )
    assert children.value.rule == "BR-W-05"


def test_work_with_no_project_can_be_started(  ) -> None:
    """ADR-0029. The absence of a project constrains nothing."""
    validate_work_transition(
        WorkStatus.TODO, WorkStatus.IN_PROGRESS, WorkTransitionContext(project_status=None)
    )


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        (ProjectStatus.ACTIVE, True),
        (ProjectStatus.ON_HOLD, True),
        (ProjectStatus.PROPOSED, False),
        (ProjectStatus.COMPLETED, False),
        (ProjectStatus.CANCELLED, False),
    ],
)
def test_starting_work_depends_on_its_project_status(status: ProjectStatus, allowed: bool) -> None:
    context = WorkTransitionContext(project_status=status)
    if allowed:
        validate_work_transition(WorkStatus.TODO, WorkStatus.IN_PROGRESS, context)
    else:
        with pytest.raises(DomainRuleViolation) as excinfo:
            validate_work_transition(WorkStatus.TODO, WorkStatus.IN_PROGRESS, context)
        assert excinfo.value.rule == "BR-W-09"


def test_completion_stamps_the_clock_and_reopening_clears_it() -> None:
    assert completed_at_for(WorkStatus.DONE, NOW) == NOW
    assert completed_at_for(WorkStatus.IN_PROGRESS, NOW) is None


# --------------------------------------------------------------------------- work creation


def test_work_requires_a_title() -> None:
    for title in ("", "   "):
        with pytest.raises(DomainRuleViolation) as excinfo:
            validate_work_creation(
                title=title, source=Source.HUMAN, project_id=None, milestone_id=None
            )
        assert excinfo.value.rule == "BR-W-01"


def test_work_can_be_created_with_no_project() -> None:
    """The capture case: "someone please check the IOC API before Friday"."""
    status = validate_work_creation(
        title="Check the IOC API", source=Source.HUMAN, project_id=None, milestone_id=None
    )
    assert status is WorkStatus.TODO


def test_a_milestone_link_requires_a_project() -> None:
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_work_creation(
            title="Ship it", source=Source.HUMAN, project_id=None, milestone_id=uuid.uuid4()
        )
    assert excinfo.value.rule == "BR-P-06"


def test_ai_cannot_create_work_directly() -> None:
    """BR-AI-16. AI-originated work arrives through an approved Proposal or not at all."""
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_work_creation(
            title="Extracted task", source=Source.AI, project_id=None, milestone_id=None
        )
    assert excinfo.value.rule == "BR-AI-16"


# --------------------------------------------------------------------------- hierarchy


def test_hierarchy_depth_is_capped_at_three() -> None:
    grandparent, parent = work_id(), work_id()
    child = work_id()
    validate_work_hierarchy(work_id=child, parent_id=parent, ancestors=[grandparent])

    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_work_hierarchy(
            work_id=work_id(), parent_id=child, ancestors=[parent, grandparent]
        )
    assert excinfo.value.rule == "BR-W-06"


def test_work_cannot_be_its_own_parent_or_ancestor() -> None:
    node = work_id()
    with pytest.raises(DomainRuleViolation):
        validate_work_hierarchy(work_id=node, parent_id=node, ancestors=[])
    with pytest.raises(DomainRuleViolation):
        validate_work_hierarchy(work_id=node, parent_id=work_id(), ancestors=[node])


def test_work_with_no_parent_is_always_valid() -> None:
    validate_work_hierarchy(work_id=work_id(), parent_id=None, ancestors=[])


# --------------------------------------------------------------------------- assignment


def test_work_may_have_no_assignment_at_all() -> None:
    """BR-W-15. There is deliberately no rule requiring one, so this is all there is."""
    assert current_owner([]) is None


def test_a_second_active_owner_is_refused() -> None:
    existing = [ExistingAssignment(uuid.uuid4(), AssignmentRole.OWNER)]
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_assignment(
            person_id=uuid.uuid4(),
            role=AssignmentRole.OWNER,
            is_primary=False,
            person_status="active",
            existing=existing,
        )
    assert excinfo.value.rule == "BR-W-13"


def test_an_ended_owner_does_not_block_a_new_one() -> None:
    existing = [
        ExistingAssignment(uuid.uuid4(), AssignmentRole.OWNER, AssignmentStatus.ENDED)
    ]
    validate_assignment(
        person_id=uuid.uuid4(),
        role=AssignmentRole.OWNER,
        is_primary=False,
        person_status="active",
        existing=existing,
    )


def test_many_contributors_and_reviewers_are_allowed() -> None:
    existing = [
        ExistingAssignment(uuid.uuid4(), AssignmentRole.CONTRIBUTOR),
        ExistingAssignment(uuid.uuid4(), AssignmentRole.CONTRIBUTOR),
        ExistingAssignment(uuid.uuid4(), AssignmentRole.REVIEWER),
    ]
    for role in (AssignmentRole.CONTRIBUTOR, AssignmentRole.REVIEWER):
        validate_assignment(
            person_id=uuid.uuid4(),
            role=role,
            is_primary=False,
            person_status="active",
            existing=existing,
        )


def test_the_same_person_cannot_hold_the_same_role_twice() -> None:
    person = uuid.uuid4()
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_assignment(
            person_id=person,
            role=AssignmentRole.CONTRIBUTOR,
            is_primary=False,
            person_status="active",
            existing=[ExistingAssignment(person, AssignmentRole.CONTRIBUTOR)],
        )
    assert excinfo.value.rule == "BR-W-14"


def test_one_person_may_hold_two_different_roles() -> None:
    person = uuid.uuid4()
    validate_assignment(
        person_id=person,
        role=AssignmentRole.REVIEWER,
        is_primary=False,
        person_status="active",
        existing=[ExistingAssignment(person, AssignmentRole.CONTRIBUTOR)],
    )


def test_only_one_assignment_may_be_primary() -> None:
    existing = [
        ExistingAssignment(uuid.uuid4(), AssignmentRole.CONTRIBUTOR, is_primary=True)
    ]
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_assignment(
            person_id=uuid.uuid4(),
            role=AssignmentRole.REVIEWER,
            is_primary=True,
            person_status="active",
            existing=existing,
        )
    assert excinfo.value.rule == "BR-W-16"


@pytest.mark.parametrize("status", ["departed", "inactive"])
def test_a_departed_person_receives_no_new_assignment(status: str) -> None:
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_assignment(
            person_id=uuid.uuid4(),
            role=AssignmentRole.OWNER,
            is_primary=False,
            person_status=status,
            existing=[],
        )
    assert excinfo.value.rule == "BR-I-05"


def test_current_owner_reads_the_assignments_and_nothing_else() -> None:
    owner = uuid.uuid4()
    assignments = [
        ExistingAssignment(uuid.uuid4(), AssignmentRole.CONTRIBUTOR),
        ExistingAssignment(owner, AssignmentRole.OWNER),
        ExistingAssignment(uuid.uuid4(), AssignmentRole.OWNER, AssignmentStatus.ENDED),
    ]
    assert current_owner(assignments) == owner


# --------------------------------------------------------------------------- dependencies


def node(kind: str = "work") -> Endpoint:
    return Endpoint(kind, uuid.uuid4())


def test_a_direct_cycle_is_detected() -> None:
    a, b = node(), node()
    existing = [DependencyEdge(a, b)]
    cycle = find_cycle(existing, DependencyEdge(b, a))
    assert cycle is not None


def test_an_indirect_cycle_is_detected_and_the_path_is_reported() -> None:
    a, b, c = node(), node(), node()
    existing = [DependencyEdge(a, b), DependencyEdge(b, c)]
    cycle = find_cycle(existing, DependencyEdge(c, a))
    assert cycle is not None
    assert [e.id for e in cycle] == [c.id, a.id, b.id, c.id]


def test_a_long_chain_without_a_cycle_is_allowed() -> None:
    chain = [node() for _ in range(8)]
    existing = [DependencyEdge(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]
    assert find_cycle(existing, DependencyEdge(chain[-1], node())) is None


def test_a_diamond_is_not_a_cycle() -> None:
    root, left, right, tip = node(), node(), node(), node()
    existing = [
        DependencyEdge(root, left),
        DependencyEdge(root, right),
        DependencyEdge(left, tip),
    ]
    assert find_cycle(existing, DependencyEdge(right, tip)) is None


def test_soft_informs_edges_do_not_constrain_scheduling() -> None:
    a, b = node(), node()
    existing = [DependencyEdge(a, b, kind=DependencyKind.INFORMS)]
    assert find_cycle(existing, DependencyEdge(b, a)) is None
    assert find_cycle(existing, DependencyEdge(b, a, kind=DependencyKind.INFORMS)) is None


def test_withdrawn_edges_do_not_constrain_scheduling() -> None:
    a, b = node(), node()
    existing = [DependencyEdge(a, b, status=DependencyStatus.WITHDRAWN)]
    assert find_cycle(existing, DependencyEdge(b, a)) is None


def test_cycles_across_work_and_milestones_are_detected() -> None:
    w, m = node("work"), node("milestone")
    existing = [DependencyEdge(w, m)]
    assert find_cycle(existing, DependencyEdge(m, w)) is not None


def test_self_dependency_is_refused() -> None:
    a = node()
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_dependency(
            candidate=DependencyEdge(a, a), existing=[], blocker_org=ORG, blocked_org=ORG
        )
    assert excinfo.value.rule == "BR-D-01"


def test_cross_organization_dependency_is_refused() -> None:
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_dependency(
            candidate=DependencyEdge(node(), node()),
            existing=[],
            blocker_org=ORG,
            blocked_org=uuid.uuid4(),
        )
    assert excinfo.value.rule == "BR-D-04"


def test_a_duplicate_dependency_is_refused() -> None:
    a, b = node(), node()
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_dependency(
            candidate=DependencyEdge(a, b),
            existing=[DependencyEdge(a, b)],
            blocker_org=ORG,
            blocked_org=ORG,
        )
    assert excinfo.value.rule == "BR-D-06"


def test_an_endpoint_must_be_work_or_milestone() -> None:
    with pytest.raises(DomainRuleViolation):
        Endpoint("commitment", uuid.uuid4())


# --------------------------------------------------------------------------- project & milestone


def test_a_project_needs_a_name_and_an_owner() -> None:
    with pytest.raises(DomainRuleViolation) as no_name:
        validate_project_creation(
            name="  ",
            owning_team_id=uuid.uuid4(),
            department_id=None,
            start_date=None,
            target_date=None,
        )
    assert no_name.value.rule == "BR-P-01"

    with pytest.raises(DomainRuleViolation) as no_owner:
        validate_project_creation(
            name="Migration",
            owning_team_id=None,
            department_id=None,
            start_date=None,
            target_date=None,
        )
    assert no_owner.value.rule == "BR-P-01"


def test_a_target_date_cannot_precede_the_start() -> None:
    with pytest.raises(DomainRuleViolation) as excinfo:
        validate_project_creation(
            name="Migration",
            owning_team_id=uuid.uuid4(),
            department_id=None,
            start_date=dt.date(2026, 6, 1),
            target_date=dt.date(2026, 5, 1),
        )
    assert excinfo.value.rule == "BR-P-02"


def test_a_project_cannot_complete_with_open_milestones_or_live_work() -> None:
    with pytest.raises(DomainRuleViolation):
        validate_project_transition(
            ProjectStatus.ACTIVE,
            ProjectStatus.COMPLETED,
            ProjectClosureContext(open_milestones=1),
        )
    with pytest.raises(DomainRuleViolation):
        validate_project_transition(
            ProjectStatus.ACTIVE,
            ProjectStatus.COMPLETED,
            ProjectClosureContext(work_in_progress_or_blocked=2),
        )
    validate_project_transition(
        ProjectStatus.ACTIVE, ProjectStatus.COMPLETED, ProjectClosureContext()
    )


def test_milestone_transitions_follow_the_declared_machine() -> None:
    validate_milestone_transition(MilestoneStatus.PLANNED, MilestoneStatus.IN_PROGRESS)
    validate_milestone_transition(MilestoneStatus.MISSED, MilestoneStatus.ACHIEVED)
    with pytest.raises(DomainRuleViolation):
        validate_milestone_transition(MilestoneStatus.CANCELLED, MilestoneStatus.ACHIEVED)


# --------------------------------------------------------------------------- reporting


def test_the_partition_is_derived_from_the_project_and_nothing_else() -> None:
    assert partition_of(None) is WorkPartition.NON_PROJECT
    assert partition_of(uuid.uuid4()) is WorkPartition.PROJECT


def test_counts_split_project_and_non_project_work() -> None:
    project = uuid.uuid4()
    counts = count_by_partition([project, None, project, None, None])
    assert (counts.project, counts.non_project, counts.total) == (2, 3, 5)


def test_a_project_rollup_excludes_non_project_work() -> None:
    """BR-RPT-02. The rule most likely to be broken by a convenient join."""
    project = uuid.uuid4()
    other = uuid.uuid4()
    rollup = roll_up_project(
        project,
        [
            (project, WorkStatus.IN_PROGRESS),
            (project, WorkStatus.DONE),
            (None, WorkStatus.IN_PROGRESS),
            (None, WorkStatus.TODO),
            (other, WorkStatus.IN_PROGRESS),
        ],
    )
    assert rollup.open_work == 1
    assert rollup.done_work == 1
    assert rollup.excluded_non_project_work == 2
