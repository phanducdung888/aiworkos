"""Work Core application services, end to end against PostgreSQL.

These prove the seam the checkpoint exists to build: a call is authorized, validated in the domain,
written through the repository, audited in the same transaction, and emitted to the outbox — in
that order, with nothing left behind when any step refuses.

Everything runs against a real database because every one of those steps has a database-side
counterpart. A service test that mocked the repository would pass while the partial unique index,
the hierarchy trigger and RLS all disagreed with it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contexts.work.domain import AssignmentRole, DomainRuleViolation
from app.contexts.work.public import (
    AssignmentService,
    AssignWork,
    ChangeDependencyStatus,
    ChangeMilestoneStatus,
    ChangeProjectStatus,
    ChangeProjectVisibility,
    ChangeWorkStatus,
    ChangeWorkVisibility,
    CreateDependency,
    CreateMilestone,
    CreateProject,
    CreateWork,
    DependencyService,
    DependencyStatus,
    EndAssignment,
    EntityNotFound,
    MilestoneService,
    MilestoneStatus,
    ProjectService,
    ProjectStatus,
    ReassignOwner,
    ServiceContext,
    Source,
    UpdateProject,
    UpdateWork,
    WorkService,
    WorkStatus,
)
from app.platform.actor import Actor, ActorType
from app.platform.authz import AuthorizationError, Principal, Role
from app.platform.concurrency import StaleVersionError
from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- helpers


def context(
    session: Session, org: WorkOrg, person_id: uuid.UUID, *roles: Role
) -> ServiceContext:
    return ServiceContext(
        session=session,
        principal=Principal(
            person_id=person_id, org_id=org.org_id, roles=frozenset(roles)
        ),
        actor=Actor(type=ActorType.PERSON, person_id=person_id, request_id="req-test"),
    )


def audit_rows(session: Session, resource_id: uuid.UUID) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in session.execute(
            text(
                "SELECT action, resource_type, actor, before_state, after_state, "
                "authorization_context FROM audit_entry WHERE resource_id = :id "
                "ORDER BY occurred_at, id"
            ),
            {"id": resource_id},
        )
        .mappings()
        .all()
    ]


def outbox_rows(session: Session, aggregate_id: uuid.UUID) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in session.execute(
            text(
                "SELECT type, aggregate_type, payload, actor FROM outbox "
                "WHERE aggregate_id = :id ORDER BY id"
            ),
            {"id": aggregate_id},
        )
        .mappings()
        .all()
    ]


def a_project(session: Session, org: WorkOrg, **overrides: object) -> uuid.UUID:
    service = ProjectService(context(session, org, org.admin, Role.ORG_ADMIN))
    project = service.create(
        CreateProject(
            name=str(overrides.get("name", "Migration")),
            owning_team_id=org.team_id,
        )
    )
    return project.id


def an_active_project(session: Session, org: WorkOrg) -> uuid.UUID:
    project_id = a_project(session, org)
    service = ProjectService(context(session, org, org.admin, Role.ORG_ADMIN))
    project = service.change_status(
        ChangeProjectStatus(
            project_id=project_id, expected_version=1, target=ProjectStatus.ACTIVE
        )
    )
    return project.id


def some_work(
    session: Session, org: WorkOrg, *, project_id: uuid.UUID | None = None, title: str = "Ship it"
) -> uuid.UUID:
    service = WorkService(context(session, org, org.admin, Role.ORG_ADMIN))
    return service.create(CreateWork(title=title, project_id=project_id)).id


# --------------------------------------------------------------------------- the seam


def test_creating_a_project_writes_the_row_its_audit_and_its_event(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """The whole point of the checkpoint, asserted once in full."""
    service = ProjectService(context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN))
    project = service.create(
        CreateProject(name="Migration", owning_team_id=work_org.team_id, objective="Move")
    )

    assert project.status == ProjectStatus.PROPOSED.value
    assert project.org_id == work_org.org_id

    audit = audit_rows(scoped_session, project.id)
    assert len(audit) == 1
    assert audit[0]["action"] == "create"
    assert audit[0]["resource_type"] == "project"
    assert audit[0]["before_state"] is None
    assert audit[0]["after_state"]["name"] == "Migration"  # type: ignore[index]
    # The decision is recorded, not just the outcome: an audit trail that cannot say *why* access
    # was allowed cannot answer the question anyone actually asks of it.
    assert audit[0]["authorization_context"]["role"] == "org_admin"  # type: ignore[index]
    assert audit[0]["actor"]["person_id"] == str(work_org.admin)  # type: ignore[index]

    events = outbox_rows(scoped_session, project.id)
    assert [e["type"] for e in events] == ["ProjectCreated"]


def test_a_refused_call_writes_nothing_at_all(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """Authorization runs first, so a denial leaves no row, no audit entry and no event."""
    before_audit = scoped_session.execute(
        text("SELECT count(*) FROM audit_entry")
    ).scalar_one()
    before_outbox = scoped_session.execute(text("SELECT count(*) FROM outbox")).scalar_one()

    service = ProjectService(context(scoped_session, work_org, work_org.member, Role.MEMBER))
    with pytest.raises(AuthorizationError):
        service.create(CreateProject(name="Sneaky", owning_team_id=work_org.team_id))

    assert scoped_session.execute(text("SELECT count(*) FROM project")).scalar_one() == 0
    assert (
        scoped_session.execute(text("SELECT count(*) FROM audit_entry")).scalar_one()
        == before_audit
    )
    assert (
        scoped_session.execute(text("SELECT count(*) FROM outbox")).scalar_one()
        == before_outbox
    )


# --------------------------------------------------------------------------- authorization


def test_a_team_lead_may_create_a_project_for_their_own_team_only(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """PROJECT.CREATE grants `team_lead` a TEAM condition, which has to be able to fail."""
    service = ProjectService(
        context(scoped_session, work_org, work_org.team_lead, Role.TEAM_LEAD)
    )
    mine = service.create(CreateProject(name="Ours", owning_team_id=work_org.team_id))
    assert mine.owning_team_id == work_org.team_id

    with pytest.raises(AuthorizationError):
        service.create(CreateProject(name="Theirs", owning_team_id=work_org.other_team_id))


def test_a_department_lead_reaches_a_project_through_its_owning_team(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """The team carries the department, so the project need not name one (BR-I-02)."""
    service = ProjectService(
        context(scoped_session, work_org, work_org.dept_lead, Role.DEPARTMENT_LEAD)
    )
    project = service.create(CreateProject(name="Delivery", owning_team_id=work_org.team_id))
    assert project.department_id is None

    with pytest.raises(AuthorizationError):
        service.create(CreateProject(name="Elsewhere", owning_team_id=work_org.other_team_id))


def test_a_member_may_create_work_that_belongs_to_no_project(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """ADR-0029 and BR-W-07, at the only layer that can actually refuse it.

    The matrix gives `member` an ORG grant on WORK.CREATE precisely so that work with no project,
    no team and no assignee has somewhere to land. If this ever fails, capture is broken.
    """
    service = WorkService(context(scoped_session, work_org, work_org.member, Role.MEMBER))
    work = service.create(CreateWork(title="Check the IOC API"))

    assert work.project_id is None
    assert work.status == WorkStatus.TODO.value
    assert (
        scoped_session.execute(
            text("SELECT count(*) FROM work_assignment WHERE work_id = :id"), {"id": work.id}
        ).scalar_one()
        == 0
    )
    events = outbox_rows(scoped_session, work.id)
    assert events[0]["payload"]["partition"] == "non_project"  # type: ignore[index]


def test_a_member_cannot_take_ownership_away_from_someone(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """ADR-0032: editing work you own and deciding who owns it are different powers."""
    work_id = some_work(scoped_session, work_org)
    admin_assignments = AssignmentService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    )
    admin_assignments.assign(
        AssignWork(work_id=work_id, person_id=work_org.member, role=AssignmentRole.OWNER)
    )

    member_assignments = AssignmentService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    )
    with pytest.raises(AuthorizationError):
        member_assignments.reassign_owner(
            ReassignOwner(work_id=work_id, person_id=work_org.outsider)
        )


def test_a_member_may_step_off_work_they_hold(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """END_ASSIGNMENT grants `member` a PERSONAL condition; holding it is the relation."""
    work_id = some_work(scoped_session, work_org)
    assignment = AssignmentService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    ).assign(AssignWork(work_id=work_id, person_id=work_org.member))

    ended = AssignmentService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).end(EndAssignment(assignment_id=assignment.id, expected_version=assignment.version))

    assert ended.status == "ended"
    assert ended.ended_at is not None
    # BR-W-14: the row is updated, never removed.
    assert (
        scoped_session.execute(
            text("SELECT count(*) FROM work_assignment WHERE id = :id"), {"id": assignment.id}
        ).scalar_one()
        == 1
    )


def test_an_unrelated_member_cannot_update_work(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """WORK.UPDATE gives `member` only a PERSONAL grant, and assignment is the relation."""
    work_id = some_work(scoped_session, work_org)
    service = WorkService(context(scoped_session, work_org, work_org.outsider, Role.MEMBER))
    with pytest.raises(AuthorizationError):
        service.update(UpdateWork(work_id=work_id, expected_version=1, title="Mine now"))

    AssignmentService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    ).assign(AssignWork(work_id=work_id, person_id=work_org.outsider))

    # A new service instance, because reach is resolved once per request by design.
    updated = WorkService(
        context(scoped_session, work_org, work_org.outsider, Role.MEMBER)
    ).update(UpdateWork(work_id=work_id, expected_version=1, title="Mine now"))
    assert updated.title == "Mine now"


def test_a_service_scopes_to_the_principals_organization_not_the_session(
    scoped_session: Session, work_org: WorkOrg, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Application scoping stands on its own, ahead of RLS (BR-G-01a).

    The session here is bound to the fixture organization, and the principal is not. The work must
    be unreachable, and unreachable in the way that says nothing about whether it exists (PQ-2).
    """
    work_id = some_work(scoped_session, work_org)
    other_org, _ = two_orgs
    stranger_id = uuid.uuid4()
    stranger = ServiceContext(
        session=scoped_session,
        principal=Principal(
            person_id=stranger_id, org_id=other_org, roles=frozenset({Role.ORG_ADMIN})
        ),
        actor=Actor(type=ActorType.PERSON, person_id=stranger_id),
    )
    with pytest.raises(EntityNotFound):
        WorkService(stranger).update(
            UpdateWork(work_id=work_id, expected_version=1, title="Reached across")
        )


def test_the_actor_and_the_principal_must_be_the_same_person(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-AI-03 at its simplest: authority is not something you can address to somebody else."""
    with pytest.raises(ValueError, match="delegated principal"):
        ServiceContext(
            session=scoped_session,
            principal=Principal(
                person_id=work_org.member,
                org_id=work_org.org_id,
                roles=frozenset({Role.MEMBER}),
            ),
            actor=Actor(type=ActorType.PERSON, person_id=work_org.admin),
        )


# --------------------------------------------------------------------------- authorship (W-1)


def test_the_person_who_captured_work_can_still_edit_it_with_nobody_assigned(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """W-1, the defect migration 0006 exists to fix.

    Before `created_by_person_id`, the PERSONAL relation on Work came only from `WorkAssignment`, so
    this exact sequence — the thing the product is for — ended in a permission error: a member
    captures work, assigns nobody because BR-W-15 says they need not, and can then do nothing with
    what they wrote.
    """
    service = WorkService(context(scoped_session, work_org, work_org.member, Role.MEMBER))
    work = service.create(CreateWork(title="Check the IOC API"))
    assert work.created_by_person_id == work_org.member

    updated = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).update(
        UpdateWork(
            work_id=work.id, expected_version=work.version, title="Check the IOC API by Friday"
        )
    )
    assert updated.title == "Check the IOC API by Friday"


def test_the_capturing_person_can_carry_their_own_work_to_done(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """PO decision (Checkpoint 3.5): authorship reaches CHANGE_STATE as well as UPDATE.

    Without it W-1 is only half fixed — a member could correct the wording of work they captured
    and never mark it finished.
    """
    work = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).create(CreateWork(title="Check the IOC API"))

    started = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).change_status(
        ChangeWorkStatus(
            work_id=work.id, expected_version=work.version, target=WorkStatus.IN_PROGRESS
        )
    )
    done = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).change_status(
        ChangeWorkStatus(
            work_id=work.id, expected_version=started.version, target=WorkStatus.DONE
        )
    )
    assert done.status == WorkStatus.DONE.value


def test_authorship_survives_the_arrival_of_an_owner(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """"Permanently", per the PO decision. Handing the work to somebody does not unwrite it."""
    work = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).create(CreateWork(title="Check the IOC API"))
    AssignmentService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    ).assign(
        AssignWork(
            work_id=work.id, person_id=work_org.outsider, role=AssignmentRole.OWNER
        )
    )

    updated = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).update(
        UpdateWork(work_id=work.id, expected_version=work.version, description="context")
    )
    assert updated.description == "context"


def test_authorship_does_not_reach_ownership_or_visibility(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """The other half of the PO decision, and the half that keeps it from being a back door.

    Writing something down is a claim on the description of the work. It is not a claim on who is
    allowed to do it, nor on who is allowed to see it.
    """
    work = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).create(CreateWork(title="Check the IOC API"))
    owner_assignment = AssignmentService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    ).assign(
        AssignWork(
            work_id=work.id, person_id=work_org.outsider, role=AssignmentRole.OWNER
        )
    )

    with pytest.raises(AuthorizationError):
        AssignmentService(
            context(scoped_session, work_org, work_org.member, Role.MEMBER)
        ).reassign_owner(ReassignOwner(work_id=work.id, person_id=work_org.member))

    with pytest.raises(AuthorizationError):
        AssignmentService(
            context(scoped_session, work_org, work_org.member, Role.MEMBER)
        ).end(
            EndAssignment(
                assignment_id=owner_assignment.id,
                expected_version=owner_assignment.version,
            )
        )

    with pytest.raises(AuthorizationError):
        WorkService(
            context(scoped_session, work_org, work_org.member, Role.MEMBER)
        ).change_visibility(
            ChangeWorkVisibility(
                work_id=work.id, expected_version=work.version, visibility="organization"
            )
        )


def test_every_created_entity_records_who_created_it(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """CLAUDE.md §6, on all five Work Core tables rather than the one that needed it first."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = an_active_project(scoped_session, work_org)
    milestone = MilestoneService(admin).create(
        CreateMilestone(project_id=project_id, name="Cutover")
    )
    work_id = some_work(scoped_session, work_org, project_id=project_id)
    other_work = some_work(scoped_session, work_org, title="Other")
    assignment = AssignmentService(admin).assign(
        AssignWork(work_id=work_id, person_id=work_org.member)
    )
    dependency = DependencyService(admin).create(
        CreateDependency(
            blocker_type="work",
            blocker_id=other_work,
            blocked_type="work",
            blocked_id=work_id,
        )
    )

    for table, row_id in (
        ("project", project_id),
        ("milestone", milestone.id),
        ("work", work_id),
        ("work_assignment", assignment.id),
        ("dependency", dependency.id),
    ):
        recorded = scoped_session.execute(
            text(f"SELECT created_by_person_id FROM {table} WHERE id = :id"),  # noqa: S608
            {"id": row_id},
        ).scalar_one()
        assert recorded == work_org.admin, f"{table} did not record its creator"


def test_a_creator_may_claim_their_own_capture_but_not_staff_it(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """D1 (Checkpoint 4a), both halves, because only together are they the rule.

    Taking work you wrote down is the capture flow principle P4 rests on: paste a conversation, get
    a Work item nobody is on, claim it. Handing that work to somebody else is staffing, and having
    written the note confers no authority over another person's time — which is the distinction
    `ASSIGN` and `REASSIGN` were split apart to hold (ADR-0032).
    """
    member = context(scoped_session, work_org, work_org.member, Role.MEMBER)
    work = WorkService(member).create(CreateWork(title="Check the IOC API"))

    claimed = AssignmentService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).assign(
        AssignWork(
            work_id=work.id, person_id=work_org.member, role=AssignmentRole.OWNER
        )
    )
    assert claimed.person_id == work_org.member
    assert claimed.role == AssignmentRole.OWNER.value

    other = WorkService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).create(CreateWork(title="Somebody should look at this"))
    with pytest.raises(AuthorizationError):
        AssignmentService(
            context(scoped_session, work_org, work_org.member, Role.MEMBER)
        ).assign(AssignWork(work_id=other.id, person_id=work_org.outsider))


def test_authorship_does_not_let_a_creator_claim_work_written_by_someone_else(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """The paired condition is a conjunction. Neither half alone opens the door."""
    work = WorkService(
        context(scoped_session, work_org, work_org.outsider, Role.MEMBER)
    ).create(CreateWork(title="Not yours"))

    with pytest.raises(AuthorizationError):
        AssignmentService(
            context(scoped_session, work_org, work_org.member, Role.MEMBER)
        ).assign(
            AssignWork(
                work_id=work.id, person_id=work_org.member, role=AssignmentRole.OWNER
            )
        )


# --------------------------------------------------------------------------- the two-step bypass


def test_a_member_cannot_reach_reassignment_through_end_then_assign(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """The hole that made the ASSIGN/REASSIGN distinction decorative (Checkpoint 3.5, item 5).

    REASSIGN denies `member` outright, but END_ASSIGNMENT and ASSIGN both grant them PERSONAL. So
    the denial held only as long as nobody made two calls instead of one: end the incumbent OWNER's
    assignment, then assign yourself. Two permitted steps composing into a forbidden one.

    The fix is in what PERSONAL *means* on an assignment — the assignment is mine, not I am related
    to its work — so step one now fails and step two never gets its chance.
    """
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    owner_assignment = AssignmentService(admin).assign(
        AssignWork(
            work_id=work_id, person_id=work_org.outsider, role=AssignmentRole.OWNER
        )
    )
    # The member is legitimately on this work, which is what used to carry them into the incumbent's
    # assignment.
    own_assignment = AssignmentService(admin).assign(
        AssignWork(
            work_id=work_id, person_id=work_org.member, role=AssignmentRole.CONTRIBUTOR
        )
    )

    with pytest.raises(AuthorizationError):
        AssignmentService(
            context(scoped_session, work_org, work_org.member, Role.MEMBER)
        ).end(
            EndAssignment(
                assignment_id=owner_assignment.id,
                expected_version=owner_assignment.version,
            )
        )

    owner = scoped_session.execute(
        text("SELECT person_id FROM work_current_owner WHERE work_id = :id"), {"id": work_id}
    ).scalar_one()
    assert owner == work_org.outsider

    # And the capability the matrix cell is actually for still works: stepping off your own work.
    ended = AssignmentService(
        context(scoped_session, work_org, work_org.member, Role.MEMBER)
    ).end(
        EndAssignment(
            assignment_id=own_assignment.id, expected_version=own_assignment.version
        )
    )
    assert ended.status == "ended"


# --------------------------------------------------------------------------- domain wiring


def test_an_undeclared_transition_is_refused(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-W-03. `todo → done` is not an edge, however convenient it would be."""
    work_id = some_work(scoped_session, work_org)
    service = WorkService(context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN))
    with pytest.raises(DomainRuleViolation, match="BR-W-03"):
        service.change_status(
            ChangeWorkStatus(work_id=work_id, expected_version=1, target=WorkStatus.DONE)
        )


def test_blocking_requires_a_cause(scoped_session: Session, work_org: WorkOrg) -> None:
    """BR-W-04."""
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    started = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=work_id, expected_version=1, target=WorkStatus.IN_PROGRESS)
    )
    assert started.started_at is not None

    with pytest.raises(DomainRuleViolation, match="BR-W-04"):
        WorkService(admin).change_status(
            ChangeWorkStatus(
                work_id=work_id, expected_version=started.version, target=WorkStatus.BLOCKED
            )
        )

    blocked = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=work_id,
            expected_version=started.version,
            target=WorkStatus.BLOCKED,
            blocked_reason="waiting on legal",
        )
    )
    assert blocked.blocked_reason == "waiting on legal"


def test_leaving_blocked_clears_the_reason_that_justified_it(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    v = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=work_id, expected_version=1, target=WorkStatus.IN_PROGRESS)
    ).version
    v = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=work_id,
            expected_version=v,
            target=WorkStatus.BLOCKED,
            blocked_reason="waiting on legal",
        )
    ).version
    resumed = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=work_id, expected_version=v, target=WorkStatus.IN_PROGRESS)
    )
    assert resumed.blocked_reason is None


def test_completion_stamps_the_clock_and_announces_itself(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-W-08, and the separate WorkCompleted event rollups and commitments listen for."""
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    started = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=work_id, expected_version=1, target=WorkStatus.IN_PROGRESS)
    )
    done = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=work_id, expected_version=started.version, target=WorkStatus.DONE
        )
    )
    assert done.completed_at is not None

    # Compared as a multiset, not a sequence. `uuid7()` randomises the sub-millisecond tail, so
    # events appended inside one transaction have no guaranteed order by id — noted in
    # progress.md, because the outbox relay claims one.
    types = sorted(str(e["type"]) for e in outbox_rows(scoped_session, work_id))
    assert types == [
        "WorkCompleted",
        "WorkCreated",
        "WorkStatusChanged",
        "WorkStatusChanged",
    ]

    reopened = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=work_id, expected_version=done.version, target=WorkStatus.IN_PROGRESS
        )
    )
    assert reopened.completed_at is None


def test_started_at_records_when_work_began_not_when_it_last_resumed(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-W-17, over the full done → reopen → done cycle.

    Two clocks that sound alike and are not: `completed_at` is about the most recent completion and
    is cleared on reopen (BR-W-08), `started_at` is about the beginning and never moves. Cycle time
    measured against a `started_at` that resets on every reopen would quietly under-report exactly
    the work that went badly.
    """
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)

    started = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=work_id, expected_version=1, target=WorkStatus.IN_PROGRESS)
    )
    began_at = started.started_at
    assert began_at is not None

    done = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=work_id, expected_version=started.version, target=WorkStatus.DONE
        )
    )
    # Read the value out now. Repository reads refresh in place, so every service call in this
    # session hands back the same `Work` instance and `done.completed_at` moves under us.
    first_completed_at = done.completed_at
    assert first_completed_at is not None

    reopened = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=work_id, expected_version=done.version, target=WorkStatus.IN_PROGRESS
        )
    )
    assert reopened.started_at == began_at
    assert reopened.completed_at is None

    finished_again = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=work_id, expected_version=reopened.version, target=WorkStatus.DONE
        )
    )
    assert finished_again.started_at == began_at
    assert finished_again.completed_at is not None
    assert finished_again.completed_at > first_completed_at


def test_work_in_a_proposed_project_cannot_be_started(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-W-09, and its deliberate exemption for project-less work."""
    project_id = a_project(scoped_session, work_org)
    work_id = some_work(scoped_session, work_org, project_id=project_id)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    with pytest.raises(DomainRuleViolation, match="BR-W-09"):
        WorkService(admin).change_status(
            ChangeWorkStatus(
                work_id=work_id, expected_version=1, target=WorkStatus.IN_PROGRESS
            )
        )

    loose_id = some_work(scoped_session, work_org, title="Unparented")
    started = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=loose_id, expected_version=1, target=WorkStatus.IN_PROGRESS)
    )
    assert started.status == WorkStatus.IN_PROGRESS.value


def test_a_milestone_outside_the_projects_own_set_is_refused(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-P-06."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    first = a_project(scoped_session, work_org, name="First")
    second = a_project(scoped_session, work_org, name="Second")
    milestone = MilestoneService(admin).create(
        CreateMilestone(project_id=second, name="Cutover")
    )
    with pytest.raises(DomainRuleViolation, match="BR-P-06"):
        WorkService(admin).create(
            CreateWork(title="Wrong project", project_id=first, milestone_id=milestone.id)
        )


def test_only_one_owner_is_active_at_a_time(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-W-13, refused by the domain with a message rather than by the index with an error code."""
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    AssignmentService(admin).assign(
        AssignWork(work_id=work_id, person_id=work_org.member, role=AssignmentRole.OWNER)
    )
    with pytest.raises(DomainRuleViolation, match="BR-W-13"):
        AssignmentService(admin).assign(
            AssignWork(
                work_id=work_id, person_id=work_org.outsider, role=AssignmentRole.OWNER
            )
        )


def test_reassigning_ownership_ends_the_incumbent_and_installs_the_successor(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    AssignmentService(admin).assign(
        AssignWork(work_id=work_id, person_id=work_org.member, role=AssignmentRole.OWNER)
    )
    successor = AssignmentService(admin).reassign_owner(
        ReassignOwner(work_id=work_id, person_id=work_org.outsider)
    )

    assert successor.person_id == work_org.outsider
    owner = scoped_session.execute(
        text("SELECT person_id FROM work_current_owner WHERE work_id = :id"), {"id": work_id}
    ).scalar_one()
    assert owner == work_org.outsider
    # History survives the handover (BR-W-14).
    assert (
        scoped_session.execute(
            text("SELECT count(*) FROM work_assignment WHERE work_id = :id"), {"id": work_id}
        ).scalar_one()
        == 2
    )


def test_a_departed_person_receives_no_new_assignment(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-I-05."""
    work_id = some_work(scoped_session, work_org)
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    with pytest.raises(DomainRuleViolation, match="BR-I-05"):
        AssignmentService(admin).assign(
            AssignWork(work_id=work_id, person_id=work_org.departed)
        )


def test_ai_originated_work_cannot_be_created_directly(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-AI-16. There is no AI write path in Phase 1, and the service is where that is true."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    with pytest.raises(DomainRuleViolation, match="BR-AI-16"):
        WorkService(admin).create(CreateWork(title="Extracted", source=Source.AI))


# --------------------------------------------------------------------------- dependencies


def test_a_cycle_is_refused_and_names_the_path(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-D-02."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    first = some_work(scoped_session, work_org, title="A")
    second = some_work(scoped_session, work_org, title="B")
    DependencyService(admin).create(
        CreateDependency(
            blocker_type="work", blocker_id=first, blocked_type="work", blocked_id=second
        )
    )
    with pytest.raises(DomainRuleViolation, match="BR-D-02"):
        DependencyService(admin).create(
            CreateDependency(
                blocker_type="work", blocker_id=second, blocked_type="work", blocked_id=first
            )
        )


def test_a_duplicate_dependency_is_refused(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-D-06."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    first = some_work(scoped_session, work_org, title="A")
    second = some_work(scoped_session, work_org, title="B")
    command = CreateDependency(
        blocker_type="work", blocker_id=first, blocked_type="work", blocked_id=second
    )
    DependencyService(admin).create(command)
    with pytest.raises(DomainRuleViolation, match="BR-D-06"):
        DependencyService(admin).create(command)


def test_a_dependency_on_something_that_does_not_exist_is_refused(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    work_id = some_work(scoped_session, work_org)
    with pytest.raises(EntityNotFound):
        DependencyService(admin).create(
            CreateDependency(
                blocker_type="work",
                blocker_id=uuid.uuid4(),
                blocked_type="work",
                blocked_id=work_id,
            )
        )


def test_completing_a_blocker_resolves_the_dependency_without_moving_the_blocked_item(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-D-03, on the narrow reading recorded in progress.md.

    The blocked item becomes free to move. It is not moved: status is a business field a person
    sets, and writing one on somebody else's work as a side effect of finishing your own is the
    automatic edit BR-AI-29 rules out.
    """
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    blocker = some_work(scoped_session, work_org, title="Blocker")
    blocked = some_work(scoped_session, work_org, title="Blocked")
    dependency = DependencyService(admin).create(
        CreateDependency(
            blocker_type="work", blocker_id=blocker, blocked_type="work", blocked_id=blocked
        )
    )

    started = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=blocked, expected_version=1, target=WorkStatus.IN_PROGRESS)
    )
    blocked_now = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=blocked, expected_version=started.version, target=WorkStatus.BLOCKED
        )
    )
    assert blocked_now.status == WorkStatus.BLOCKED.value

    v = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=blocker, expected_version=1, target=WorkStatus.IN_PROGRESS)
    ).version
    WorkService(admin).change_status(
        ChangeWorkStatus(work_id=blocker, expected_version=v, target=WorkStatus.DONE)
    )

    settled = scoped_session.execute(
        text("SELECT status FROM dependency WHERE id = :id"), {"id": dependency.id}
    ).scalar_one()
    assert settled == DependencyStatus.RESOLVED.value

    still_blocked = scoped_session.execute(
        text("SELECT status FROM work WHERE id = :id"), {"id": blocked}
    ).scalar_one()
    assert still_blocked == WorkStatus.BLOCKED.value

    # ... and now nothing stands in its way.
    resumed = WorkService(admin).change_status(
        ChangeWorkStatus(
            work_id=blocked,
            expected_version=blocked_now.version,
            target=WorkStatus.IN_PROGRESS,
        )
    )
    assert resumed.status == WorkStatus.IN_PROGRESS.value


def test_cancelling_a_blocker_withdraws_the_dependency_rather_than_deleting_it(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-D-05."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    blocker = some_work(scoped_session, work_org, title="Blocker")
    blocked = some_work(scoped_session, work_org, title="Blocked")
    dependency = DependencyService(admin).create(
        CreateDependency(
            blocker_type="work", blocker_id=blocker, blocked_type="work", blocked_id=blocked
        )
    )
    WorkService(admin).change_status(
        ChangeWorkStatus(work_id=blocker, expected_version=1, target=WorkStatus.CANCELLED)
    )
    row = scoped_session.execute(
        text("SELECT status FROM dependency WHERE id = :id"), {"id": dependency.id}
    ).scalar_one()
    assert row == DependencyStatus.WITHDRAWN.value


def test_an_informs_dependency_never_blocks_or_settles(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """Soft edges are advisory. They must not gate completion and must not auto-resolve."""
    from app.contexts.work.public import DependencyKind

    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    blocker = some_work(scoped_session, work_org, title="Context")
    blocked = some_work(scoped_session, work_org, title="Informed")
    dependency = DependencyService(admin).create(
        CreateDependency(
            blocker_type="work",
            blocker_id=blocker,
            blocked_type="work",
            blocked_id=blocked,
            kind=DependencyKind.INFORMS,
        )
    )
    v = WorkService(admin).change_status(
        ChangeWorkStatus(work_id=blocker, expected_version=1, target=WorkStatus.IN_PROGRESS)
    ).version
    WorkService(admin).change_status(
        ChangeWorkStatus(work_id=blocker, expected_version=v, target=WorkStatus.DONE)
    )
    still_active = scoped_session.execute(
        text("SELECT status FROM dependency WHERE id = :id"), {"id": dependency.id}
    ).scalar_one()
    assert still_active == DependencyStatus.ACTIVE.value


# --------------------------------------------------------------------------- cascades


def test_cancelling_a_project_cancels_its_contents_and_records_why(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-P-04, including the part that says the cascade is recorded with its originating action."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = an_active_project(scoped_session, work_org)
    milestone = MilestoneService(admin).create(
        CreateMilestone(project_id=project_id, name="Cutover")
    )
    work_id = some_work(scoped_session, work_org, project_id=project_id)
    project_version = scoped_session.execute(
        text("SELECT version FROM project WHERE id = :id"), {"id": project_id}
    ).scalar_one()

    ProjectService(admin).change_status(
        ChangeProjectStatus(
            project_id=project_id,
            expected_version=project_version,
            target=ProjectStatus.CANCELLED,
        )
    )

    assert (
        scoped_session.execute(
            text("SELECT status FROM milestone WHERE id = :id"), {"id": milestone.id}
        ).scalar_one()
        == MilestoneStatus.CANCELLED.value
    )
    assert (
        scoped_session.execute(
            text("SELECT status FROM work WHERE id = :id"), {"id": work_id}
        ).scalar_one()
        == WorkStatus.CANCELLED.value
    )

    cascaded = audit_rows(scoped_session, work_id)[-1]
    assert cascaded["action"] == "change_state"
    origin = cascaded["actor"]["extra"]  # type: ignore[index]
    assert origin["cascaded_from"] == "project.change_status:cancelled"
    assert origin["cascade_origin_id"] == str(project_id)


def test_a_project_cannot_complete_while_its_work_is_unfinished(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-P-03."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = an_active_project(scoped_session, work_org)
    work_id = some_work(scoped_session, work_org, project_id=project_id)
    WorkService(admin).change_status(
        ChangeWorkStatus(work_id=work_id, expected_version=1, target=WorkStatus.IN_PROGRESS)
    )
    version = scoped_session.execute(
        text("SELECT version FROM project WHERE id = :id"), {"id": project_id}
    ).scalar_one()
    with pytest.raises(DomainRuleViolation, match="BR-P-03"):
        ProjectService(admin).change_status(
            ChangeProjectStatus(
                project_id=project_id,
                expected_version=version,
                target=ProjectStatus.COMPLETED,
            )
        )


def test_achieving_a_milestone_settles_what_it_was_blocking(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-D-03 reaches milestones too, which is why `achieved` is in the settlement table."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = an_active_project(scoped_session, work_org)
    milestone = MilestoneService(admin).create(
        CreateMilestone(project_id=project_id, name="Cutover")
    )
    work_id = some_work(scoped_session, work_org, project_id=project_id)
    dependency = DependencyService(admin).create(
        CreateDependency(
            blocker_type="milestone",
            blocker_id=milestone.id,
            blocked_type="work",
            blocked_id=work_id,
        )
    )
    started = MilestoneService(admin).change_status(
        ChangeMilestoneStatus(
            milestone_id=milestone.id,
            expected_version=milestone.version,
            target=MilestoneStatus.IN_PROGRESS,
        )
    )
    MilestoneService(admin).change_status(
        ChangeMilestoneStatus(
            milestone_id=milestone.id,
            expected_version=started.version,
            target=MilestoneStatus.ACHIEVED,
        )
    )
    assert (
        scoped_session.execute(
            text("SELECT status FROM dependency WHERE id = :id"), {"id": dependency.id}
        ).scalar_one()
        == DependencyStatus.RESOLVED.value
    )


# --------------------------------------------------------------------------- concurrency


def test_a_write_against_a_version_that_has_moved_on_is_refused(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-G-06. Two people editing one Work is normal; discarding one of their edits is not."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org)

    first = ProjectService(admin).update(
        UpdateProject(project_id=project_id, expected_version=1, objective="Ship by Q3")
    )
    assert first.version == 2

    with pytest.raises(StaleVersionError):
        ProjectService(admin).update(
            UpdateProject(project_id=project_id, expected_version=1, objective="Ship by Q4")
        )
    assert (
        scoped_session.execute(
            text("SELECT objective FROM project WHERE id = :id"), {"id": project_id}
        ).scalar_one()
        == "Ship by Q3"
    )


def test_an_unmentioned_field_is_left_alone_and_an_explicit_null_clears_it(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """The distinction `UNSET` exists for. Getting it wrong erases data on every partial update."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org)
    ProjectService(admin).update(
        UpdateProject(
            project_id=project_id,
            expected_version=1,
            objective="Ship",
            target_date=dt.date(2026, 12, 1),
        )
    )
    untouched = ProjectService(admin).update(
        UpdateProject(project_id=project_id, expected_version=2, objective="Ship faster")
    )
    assert untouched.target_date == dt.date(2026, 12, 1)

    cleared = ProjectService(admin).update(
        UpdateProject(
            project_id=project_id, expected_version=untouched.version, target_date=None
        )
    )
    assert cleared.target_date is None
    assert cleared.objective == "Ship faster"


# --------------------------------------------------------------------------- audit coverage


def test_every_mutating_service_call_writes_exactly_one_audit_entry_for_its_own_resource(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """BR-G-02, enumerated rather than sampled.

    Listed by hand on purpose. A reflective version would enumerate whatever exists, so a new
    service method that forgot to audit would arrive already covered by a test that cannot fail.
    Adding a method here is the moment somebody has to think about its audit entry.
    """
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = an_active_project(scoped_session, work_org)
    milestone = MilestoneService(admin).create(
        CreateMilestone(project_id=project_id, name="Cutover")
    )
    work_id = some_work(scoped_session, work_org, project_id=project_id)
    other_work = some_work(scoped_session, work_org, title="Other")
    assignment = AssignmentService(admin).assign(
        AssignWork(work_id=work_id, person_id=work_org.member)
    )
    dependency = DependencyService(admin).create(
        CreateDependency(
            blocker_type="work",
            blocker_id=other_work,
            blocked_type="work",
            blocked_id=work_id,
        )
    )

    def work_version() -> int:
        return int(
            scoped_session.execute(
                text("SELECT version FROM work WHERE id = :id"), {"id": work_id}
            ).scalar_one()
        )

    def project_version() -> int:
        return int(
            scoped_session.execute(
                text("SELECT version FROM project WHERE id = :id"), {"id": project_id}
            ).scalar_one()
        )

    operations: list[tuple[str, uuid.UUID, Callable[[], object]]] = [
        (
            "project.update",
            project_id,
            lambda: ProjectService(admin).update(
                UpdateProject(
                    project_id=project_id,
                    expected_version=project_version(),
                    objective="Ship",
                )
            ),
        ),
        (
            "project.change_visibility",
            project_id,
            lambda: ProjectService(admin).change_visibility(
                ChangeProjectVisibility(
                    project_id=project_id,
                    expected_version=project_version(),
                    visibility="team",
                )
            ),
        ),
        (
            "milestone.change_status",
            milestone.id,
            lambda: MilestoneService(admin).change_status(
                ChangeMilestoneStatus(
                    milestone_id=milestone.id,
                    expected_version=milestone.version,
                    target=MilestoneStatus.IN_PROGRESS,
                )
            ),
        ),
        (
            "work.change_visibility",
            work_id,
            # Narrowing, not widening. The project was narrowed to `team` two operations earlier,
            # which cascaded onto this Work (BR-W-19), so `organization` is now refused here — and
            # the refusal is asserted properly in test_api_project_visibility.
            lambda: WorkService(admin).change_visibility(
                ChangeWorkVisibility(
                    work_id=work_id,
                    expected_version=work_version(),
                    visibility="restricted",
                )
            ),
        ),
        (
            "dependency.change_status",
            dependency.id,
            lambda: DependencyService(admin).change_status(
                ChangeDependencyStatus(
                    dependency_id=dependency.id,
                    expected_version=dependency.version,
                    target=DependencyStatus.WITHDRAWN,
                )
            ),
        ),
        (
            "assignment.end",
            assignment.id,
            lambda: AssignmentService(admin).end(
                EndAssignment(
                    assignment_id=assignment.id, expected_version=assignment.version
                )
            ),
        ),
    ]

    # Creation is already covered above; these are the mutations that follow it.
    for label, resource_id, call in operations:
        before = len(audit_rows(scoped_session, resource_id))
        call()
        after = len(audit_rows(scoped_session, resource_id))
        assert after == before + 1, f"{label} wrote {after - before} audit entries, expected 1"
