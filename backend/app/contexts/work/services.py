"""Work Core application services.

One use case per method, and every one of them walks the same five steps in the same order:

    authorize → validate in the domain → write through the repository → audit → emit

The order is not stylistic. Authorizing last would mean deciding whether somebody may do a thing
after doing it. Auditing outside the transaction would mean history that disagrees with state. And
emitting before the write would mean publishing a fact that may yet roll back. Each step sits where
it does because the alternatives are defects.

Services own the transaction boundary in the sense that they assume one and never open their own:
the caller — a router, a worker, the Tool Gateway — supplies an `org_session`, so several service
calls can compose into one atomic unit of work. That is what makes the cascades below honest. A
project cancellation that takes its milestones and work with it (BR-P-04) either happens completely
or does not happen.

Nothing here reaches for FastAPI, and nothing here writes SQL. Both are somebody else's job, and
the architecture tests fail if that stops being true.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Session

import app.contexts.identity.public as identity
from app.contexts.work import authorization, repository
from app.contexts.work.commands import (
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
    EndAssignment,
    ReassignOwner,
    SetOwner,
    UpdateMilestone,
    UpdateProject,
    UpdateWork,
)
from app.contexts.work.domain import (
    AssignmentRole,
    AssignmentStatus,
    DependencyEdge,
    DependencyKind,
    DependencyStatus,
    Endpoint,
    MilestoneStatus,
    ProjectClosureContext,
    ProjectStatus,
    WorkStatus,
    WorkTransitionContext,
    completed_at_for,
    effective_work_visibility,
    narrowing_to,
    validate_assignment,
    validate_dependency,
    validate_milestone_transition,
    validate_project_creation,
    validate_project_transition,
    validate_work_creation,
    validate_work_hierarchy,
    validate_work_transition,
)
from app.contexts.work.models import Dependency, Milestone, Project, Work, WorkAssignment
from app.platform.actor import Actor, ActorType
from app.platform.audit import record_audit
from app.platform.authz import (
    Action,
    Decision,
    Principal,
    Relation,
    ResourceType,
    authorize,
)
from app.platform.errors import DomainRuleViolation, EntityNotFound
from app.platform.outbox import append_domain_event
from app.platform.partial import changed_fields, value_or_none


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def _snapshot(entity: DeclarativeBase) -> dict[str, Any]:
    """The row as audit wants to see it: every column, JSON-safe, no relationships."""
    mapper = sa_inspect(type(entity))
    return {attr.key: _jsonable(getattr(entity, attr.key)) for attr in mapper.column_attrs}


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceContext:
    """Who is asking, over which session.

    `principal` and `actor` are two views of the same person and are checked against each other
    here rather than trusted. For an AI actor the principal is the delegated human, which is what
    makes "AI authority never exceeds human authority" a property of the call rather than of the
    prompt (BR-AI-03).
    """

    session: Session
    principal: Principal
    actor: Actor

    def __post_init__(self) -> None:
        if self.actor.type is ActorType.SYSTEM:
            return
        if self.actor.person_id != self.principal.person_id:
            raise ValueError(
                "the actor and the authorizing principal must be the same person; "
                "an AI actor carries its delegated principal here (BR-AI-03)"
            )


class _WorkCoreService:
    def __init__(self, context: ServiceContext) -> None:
        self._ctx = context
        self._reach = authorization.reach_of(context.session, context.principal)

    # ------------------------------------------------------------------ plumbing

    @property
    def _session(self) -> Session:
        return self._ctx.session

    @property
    def _org_id(self) -> uuid.UUID:
        return self._ctx.principal.org_id

    def _authorize(
        self,
        action: Action,
        resource_type: ResourceType,
        relations: frozenset[Relation],
        resource_id: uuid.UUID | None = None,
    ) -> Decision:
        return authorize(
            self._ctx.principal,
            action,
            authorization.ref(resource_type, self._org_id, relations, resource_id),
        )

    def _audit(
        self,
        *,
        action: Action,
        resource_type: ResourceType,
        resource_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        decision: Decision | None,
        actor: Actor | None = None,
    ) -> None:
        record_audit(
            self._session,
            org_id=self._org_id,
            actor=actor or self._ctx.actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
            decision=decision,
        )

    def _emit(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        payload: dict[str, Any],
        actor: Actor | None = None,
    ) -> None:
        """Append to the outbox.

        Every state change emits. Which of these events later projects into an internal Event is a
        separate, narrower question answered by the allow-list in business-rules §9a, and answered
        in Phase 2 by the projector rather than here — the outbox is a delivery mechanism, not a
        policy.
        """
        append_domain_event(
            self._session,
            org_id=self._org_id,
            type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            actor=actor or self._ctx.actor,
        )

    @property
    def _created_by(self) -> uuid.UUID | None:
        """The person this row is attributed to, or None for a system actor.

        For an AI actor this is the delegated principal, which is the right answer: an AI-originated
        row was authored on somebody's authority and that is who the authorization relation should
        reach. The AI interaction itself is not lost — it is on the actor recorded in `audit_entry`,
        where it cannot be rewritten (BR-AI-02, BR-G-03).
        """
        return self._ctx.actor.person_id

    def _assert_references(
        self,
        *,
        owning_team_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        people: dict[str, uuid.UUID | None] | None = None,
    ) -> None:
        """ADR-0035. Every Identity reference is checked before anything is written.

        Through `identity.public`, never by reading Identity's tables: the Work Core knows how to
        ask whether a team exists and nothing about where teams are stored. Composite foreign keys
        stay exactly as they are underneath — two independent mechanisms, neither of which is the
        excuse for dropping the other (BR-G-01a) — but they are the backstop, and a backstop cannot
        name the field that was wrong (W-11).
        """
        if owning_team_id is not None:
            identity.assert_team_exists(
                self._session, org_id=self._org_id, team_id=owning_team_id, field="owning_team_id"
            )
        if department_id is not None:
            identity.assert_department_exists(
                self._session,
                org_id=self._org_id,
                department_id=department_id,
                field="department_id",
            )
        for field, person_id in (people or {}).items():
            if person_id is not None:
                identity.assert_person_exists(
                    self._session, org_id=self._org_id, person_id=person_id, field=field
                )

    def _cascade_actor(self, origin_action: str, origin_id: uuid.UUID) -> Actor:
        """The same actor, carrying why this change happened.

        BR-P-04 requires the cascade to be recorded in audit *with the originating action*. Putting
        it on the actor rather than in the state snapshot keeps the snapshot a description of the
        row and nothing else.
        """
        base = self._ctx.actor
        return dataclasses.replace(
            base,
            extra={
                **base.extra,
                "cascaded_from": origin_action,
                "cascade_origin_id": str(origin_id),
            },
        )

    # ------------------------------------------------------------------ loaders

    def _load_project(self, project_id: uuid.UUID) -> Project:
        project = repository.get_project(
            self._session, org_id=self._org_id, project_id=project_id
        )
        if project is None:
            raise EntityNotFound("project", project_id)
        return project

    def _load_milestone(self, milestone_id: uuid.UUID) -> Milestone:
        milestone = repository.get_milestone(
            self._session, org_id=self._org_id, milestone_id=milestone_id
        )
        if milestone is None:
            raise EntityNotFound("milestone", milestone_id)
        return milestone

    def _load_work(self, work_id: uuid.UUID) -> Work:
        work = repository.get_work(self._session, org_id=self._org_id, work_id=work_id)
        if work is None:
            raise EntityNotFound("work", work_id)
        return work

    def _load_assignment(self, assignment_id: uuid.UUID) -> WorkAssignment:
        assignment = repository.get_assignment(
            self._session, org_id=self._org_id, assignment_id=assignment_id
        )
        if assignment is None:
            raise EntityNotFound("work_assignment", assignment_id)
        return assignment

    def _load_dependency(self, dependency_id: uuid.UUID) -> Dependency:
        dependency = repository.get_dependency(
            self._session, org_id=self._org_id, dependency_id=dependency_id
        )
        if dependency is None:
            raise EntityNotFound("dependency", dependency_id)
        return dependency


class ProjectService(_WorkCoreService):
    def create(self, command: CreateProject) -> Project:
        validate_project_creation(
            name=command.name,
            owning_team_id=command.owning_team_id,
            department_id=command.department_id,
            start_date=command.start_date,
            target_date=command.target_date,
        )
        # Relations are computed against the project as proposed. A team lead may create a project
        # for their own team and not for somebody else's, and that is decided here, before the row
        # exists, from the values the caller supplied.
        self._assert_references(
            owning_team_id=command.owning_team_id,
            department_id=command.department_id,
            people={
                "lead_person_id": command.lead_person_id,
                "sponsor_person_id": command.sponsor_person_id,
            },
        )
        prospective = Project(
            org_id=self._org_id,
            name=command.name,
            owning_team_id=command.owning_team_id,
            department_id=command.department_id,
            lead_person_id=command.lead_person_id,
            sponsor_person_id=command.sponsor_person_id,
        )
        decision = self._authorize(
            Action.CREATE,
            ResourceType.PROJECT,
            authorization.project_relations(self._reach, prospective),
        )
        project = repository.insert_project(
            self._session,
            org_id=self._org_id,
            name=command.name,
            description=command.description,
            objective=command.objective,
            owning_team_id=command.owning_team_id,
            department_id=command.department_id,
            lead_person_id=command.lead_person_id,
            sponsor_person_id=command.sponsor_person_id,
            start_date=command.start_date,
            target_date=command.target_date,
            status=ProjectStatus.PROPOSED.value,
            visibility=command.visibility,
            source=command.source.value,
            created_by_person_id=self._created_by,
        )
        after = _snapshot(project)
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.PROJECT,
            resource_id=project.id,
            before=None,
            after=after,
            decision=decision,
        )
        self._emit(
            "ProjectCreated",
            "project",
            project.id,
            {"name": project.name, "status": project.status},
        )
        return project

    def update(self, command: UpdateProject) -> Project:
        project = self._load_project(command.project_id)
        decision = self._authorize(
            Action.UPDATE,
            ResourceType.PROJECT,
            authorization.project_relations(self._reach, project),
            project.id,
        )
        before = _snapshot(project)
        changes = changed_fields(command)
        changes.pop("project_id")
        changes.pop("expected_version")

        self._assert_references(
            owning_team_id=changes.get("owning_team_id"),
            department_id=changes.get("department_id"),
            people={
                "lead_person_id": changes.get("lead_person_id"),
                "sponsor_person_id": changes.get("sponsor_person_id"),
            },
        )
        merged = {**before, **{k: _jsonable(v) for k, v in changes.items()}}
        validate_project_creation(
            name=str(merged["name"]),
            owning_team_id=changes.get("owning_team_id", project.owning_team_id),
            department_id=changes.get("department_id", project.department_id),
            start_date=changes.get("start_date", project.start_date),
            target_date=changes.get("target_date", project.target_date),
        )

        updated = repository.update_project(
            self._session,
            org_id=self._org_id,
            project_id=project.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.PROJECT,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit("ProjectUpdated", "project", updated.id, {"changed": sorted(changes)})
        return updated

    def change_visibility(self, command: ChangeProjectVisibility) -> Project:
        project = self._load_project(command.project_id)
        decision = self._authorize(
            Action.CHANGE_VISIBILITY,
            ResourceType.PROJECT,
            authorization.project_relations(self._reach, project),
            project.id,
        )
        before = _snapshot(project)
        updated = repository.update_project(
            self._session,
            org_id=self._org_id,
            project_id=project.id,
            expected_version=command.expected_version,
            visibility=command.visibility,
        )
        self._audit(
            action=Action.CHANGE_VISIBILITY,
            resource_type=ResourceType.PROJECT,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "ProjectVisibilityChanged",
            "project",
            updated.id,
            {"from": before["visibility"], "to": updated.visibility},
        )
        self._narrow_contents(updated, decision)
        return updated

    def _narrow_contents(self, project: Project, decision: Decision) -> None:
        """BR-W-19. A narrowed Project takes its wider Work down with it.

        Only narrowing cascades. Widening a Project deliberately leaves its Work alone: access that
        was withheld should be granted by someone deciding to grant it, not as a side effect of an
        unrelated change to the container. That asymmetry is the whole point of the rule — the
        dangerous direction is the automatic one.

        Authorized by the Project decision rather than per Work, for the same reason BR-P-04's
        cascade is: leaving behind the Work a lead happens not to reach would produce a Project
        marked restricted whose contents are not, which is the state this exists to prevent.
        """
        actor = self._cascade_actor(
            f"project.change_visibility:{project.visibility}", project.id
        )
        for work in repository.work_wider_than(
            self._session,
            org_id=self._org_id,
            project_id=project.id,
            visibility=project.visibility,
        ):
            target = narrowing_to(work.visibility, project.visibility)
            if target is None:  # pragma: no cover - the query already excluded these
                continue
            before = _snapshot(work)
            narrowed = repository.update_work(
                self._session,
                org_id=self._org_id,
                work_id=work.id,
                expected_version=work.version,
                visibility=target,
            )
            self._audit(
                action=Action.CHANGE_VISIBILITY,
                resource_type=ResourceType.WORK,
                resource_id=narrowed.id,
                before=before,
                after=_snapshot(narrowed),
                decision=decision,
                actor=actor,
            )
            self._emit(
                "WorkVisibilityChanged",
                "work",
                narrowed.id,
                {"from": before["visibility"], "to": narrowed.visibility},
                actor=actor,
            )

    def change_status(self, command: ChangeProjectStatus) -> Project:
        project = self._load_project(command.project_id)
        decision = self._authorize(
            Action.CHANGE_STATE,
            ResourceType.PROJECT,
            authorization.project_relations(self._reach, project),
            project.id,
        )
        current = ProjectStatus(project.status)
        closure = ProjectClosureContext(
            open_milestones=repository.count_open_milestones(
                self._session, org_id=self._org_id, project_id=project.id
            ),
            work_in_progress_or_blocked=repository.count_work_in_progress_or_blocked(
                self._session, org_id=self._org_id, project_id=project.id
            ),
        )
        validate_project_transition(current, command.target, closure)

        before = _snapshot(project)
        values: dict[str, Any] = {"status": command.target.value}
        if command.target is ProjectStatus.COMPLETED:
            values["actual_end_date"] = dt.datetime.now(dt.UTC).date()
        updated = repository.update_project(
            self._session,
            org_id=self._org_id,
            project_id=project.id,
            expected_version=command.expected_version,
            **values,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.PROJECT,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "ProjectStatusChanged",
            "project",
            updated.id,
            {"from": current.value, "to": command.target.value},
        )

        if command.target is ProjectStatus.CANCELLED:
            self._cancel_contents(updated, decision)
        return updated

    def _cancel_contents(self, project: Project, decision: Decision) -> None:
        """BR-P-04. Cancelling a project takes its open milestones and work with it.

        Each cascaded change is authorized by the project decision, not re-authorized per item. The
        rule makes the cascade a consequence of the project action; re-deciding it item by item
        would mean a lead could cancel a project and leave behind work they happen not to have
        reach over, which is the half-cancelled state BR-P-04 exists to prevent.
        """
        actor = self._cascade_actor("project.change_status:cancelled", project.id)
        milestones = MilestoneService(self._ctx)
        work_service = WorkService(self._ctx)

        for milestone in repository.open_milestones_of_project(
            self._session, org_id=self._org_id, project_id=project.id
        ):
            milestones.apply_status(
                milestone,
                MilestoneStatus.CANCELLED,
                decision=decision,
                actor=actor,
            )

        for work in repository.open_work_of_project(
            self._session, org_id=self._org_id, project_id=project.id
        ):
            # `proposed` work has never been accepted, so it is rejected rather than cancelled:
            # the state machine offers no `proposed → cancelled` edge (BR-W-03).
            target = (
                WorkStatus.REJECTED
                if WorkStatus(work.status) is WorkStatus.PROPOSED
                else WorkStatus.CANCELLED
            )
            work_service.apply_status(
                work,
                target,
                decision=decision,
                actor=actor,
                blocked_reason=None,
            )


class MilestoneService(_WorkCoreService):
    def create(self, command: CreateMilestone) -> Milestone:
        project = self._load_project(command.project_id)
        decision = self._authorize(
            Action.CREATE,
            ResourceType.MILESTONE,
            authorization.project_relations(self._reach, project),
        )
        if not command.name.strip():
            raise DomainRuleViolation("BR-P-01", "a milestone requires a name")
        milestone = repository.insert_milestone(
            self._session,
            org_id=self._org_id,
            project_id=project.id,
            name=command.name,
            description=command.description,
            acceptance_criteria=command.acceptance_criteria,
            target_date=command.target_date,
            order_index=command.order_index,
            status=MilestoneStatus.PLANNED.value,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.MILESTONE,
            resource_id=milestone.id,
            before=None,
            after=_snapshot(milestone),
            decision=decision,
        )
        self._emit(
            "MilestoneCreated",
            "milestone",
            milestone.id,
            {"project_id": str(project.id), "name": milestone.name},
        )
        return milestone

    def update(self, command: UpdateMilestone) -> Milestone:
        milestone = self._load_milestone(command.milestone_id)
        project = authorization.milestone_project(self._session, milestone)
        decision = self._authorize(
            Action.UPDATE,
            ResourceType.MILESTONE,
            authorization.milestone_relations(self._reach, project),
            milestone.id,
        )
        before = _snapshot(milestone)
        changes = changed_fields(command)
        changes.pop("milestone_id")
        changes.pop("expected_version")
        if "name" in changes and not str(changes["name"]).strip():
            raise DomainRuleViolation("BR-P-01", "a milestone requires a name")

        updated = repository.update_milestone(
            self._session,
            org_id=self._org_id,
            milestone_id=milestone.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.MILESTONE,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit("MilestoneUpdated", "milestone", updated.id, {"changed": sorted(changes)})
        return updated

    def change_status(self, command: ChangeMilestoneStatus) -> Milestone:
        milestone = self._load_milestone(command.milestone_id)
        project = authorization.milestone_project(self._session, milestone)
        decision = self._authorize(
            Action.CHANGE_STATE,
            ResourceType.MILESTONE,
            authorization.milestone_relations(self._reach, project),
            milestone.id,
        )
        return self.apply_status(
            milestone,
            command.target,
            decision=decision,
            expected_version=command.expected_version,
        )

    def apply_status(
        self,
        milestone: Milestone,
        target: MilestoneStatus,
        *,
        decision: Decision,
        actor: Actor | None = None,
        expected_version: int | None = None,
    ) -> Milestone:
        """The status change itself, already authorized.

        Public within the context so the project cancellation cascade can reuse it without
        re-deciding authorization or duplicating the dependency consequences below.
        """
        current = MilestoneStatus(milestone.status)
        validate_milestone_transition(current, target)
        before = _snapshot(milestone)

        values: dict[str, Any] = {"status": target.value}
        if target is MilestoneStatus.ACHIEVED:
            values["actual_date"] = dt.datetime.now(dt.UTC).date()

        updated = repository.update_milestone(
            self._session,
            org_id=self._org_id,
            milestone_id=milestone.id,
            expected_version=(
                expected_version if expected_version is not None else milestone.version
            ),
            **values,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.MILESTONE,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
            actor=actor,
        )
        self._emit(
            "MilestoneStatusChanged",
            "milestone",
            updated.id,
            {"from": current.value, "to": target.value},
            actor=actor,
        )
        _settle_dependencies(
            self,
            endpoint=Endpoint(type="milestone", id=updated.id),
            blocker_reached=target.value,
            decision=decision,
            actor=actor,
        )
        return updated


class WorkService(_WorkCoreService):
    def create(self, command: CreateWork) -> Work:
        status = validate_work_creation(
            title=command.title,
            source=command.source,
            project_id=command.project_id,
            milestone_id=command.milestone_id,
        )
        project = (
            self._load_project(command.project_id)
            if command.project_id is not None
            else None
        )
        if command.milestone_id is not None:
            milestone = self._load_milestone(command.milestone_id)
            if milestone.project_id != command.project_id:
                raise DomainRuleViolation(
                    "BR-P-06",
                    "work can only reference a milestone within its own project",
                )
        visibility = effective_work_visibility(
            requested=value_or_none(command.visibility),
            project_visibility=project.visibility if project is not None else None,
            default="team",
        )
        if command.parent_work_id is not None:
            parent = self._load_work(command.parent_work_id)
            validate_work_hierarchy(
                work_id=uuid.uuid4(),  # not yet persisted; only the depth and chain matter here
                parent_id=parent.id,
                ancestors=repository.ancestor_ids(
                    self._session, org_id=self._org_id, work_id=parent.id
                ),
            )

        # Project-less work has no team to inherit from, so `member` reaches it through the ORG
        # grant on WORK.CREATE and nothing else. That row of the matrix is what makes "someone
        # should check the IOC API" capturable at all (BR-W-07, ADR-0029).
        relations = (
            authorization.project_relations(self._reach, project)
            if project is not None
            else frozenset()
        )
        decision = self._authorize(Action.CREATE, ResourceType.WORK, relations)

        work = repository.insert_work(
            self._session,
            org_id=self._org_id,
            project_id=command.project_id,
            milestone_id=command.milestone_id,
            parent_work_id=command.parent_work_id,
            title=command.title,
            description=command.description,
            type=command.type,
            status=status.value,
            priority=command.priority,
            due_date=command.due_date,
            visibility=visibility,
            source=command.source.value,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.WORK,
            resource_id=work.id,
            before=None,
            after=_snapshot(work),
            decision=decision,
        )
        self._emit(
            "WorkCreated",
            "work",
            work.id,
            {
                "title": work.title,
                "status": work.status,
                # Stated rather than implied: a consumer must never have to guess whether a null
                # project means "none" or "not loaded" (BR-RPT-01).
                "partition": "non_project" if work.project_id is None else "project",
            },
        )
        return work

    def update(self, command: UpdateWork) -> Work:
        work = self._load_work(command.work_id)
        project = authorization.work_project(self._session, work)
        decision = self._authorize(
            Action.UPDATE,
            ResourceType.WORK,
            authorization.work_authorship_relations(self._reach, work, project),
            work.id,
        )
        before = _snapshot(work)
        changes = changed_fields(command)
        changes.pop("work_id")
        changes.pop("expected_version")

        if "title" in changes and not str(changes["title"]).strip():
            raise DomainRuleViolation("BR-W-01", "work requires a non-empty title")

        next_project_id = changes.get("project_id", work.project_id)
        next_milestone_id = changes.get("milestone_id", work.milestone_id)
        if next_milestone_id is not None:
            if next_project_id is None:
                raise DomainRuleViolation(
                    "BR-P-06", "work can only reference a milestone within its own project"
                )
            milestone = self._load_milestone(next_milestone_id)
            if milestone.project_id != next_project_id:
                raise DomainRuleViolation(
                    "BR-P-06", "work can only reference a milestone within its own project"
                )
        if "parent_work_id" in changes and changes["parent_work_id"] is not None:
            parent_id = changes["parent_work_id"]
            validate_work_hierarchy(
                work_id=work.id,
                parent_id=parent_id,
                ancestors=repository.ancestor_ids(
                    self._session, org_id=self._org_id, work_id=parent_id
                ),
            )

        updated = repository.update_work(
            self._session,
            org_id=self._org_id,
            work_id=work.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.WORK,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit("WorkUpdated", "work", updated.id, {"changed": sorted(changes)})
        return updated

    def change_visibility(self, command: ChangeWorkVisibility) -> Work:
        work = self._load_work(command.work_id)
        project = authorization.work_project(self._session, work)
        decision = self._authorize(
            Action.CHANGE_VISIBILITY,
            ResourceType.WORK,
            authorization.work_relations(self._reach, work, project),
            work.id,
        )
        # BR-W-19 applies to a later change as well as to creation. Without this, the rule would
        # hold for exactly one moment in a Work item's life.
        effective_work_visibility(
            requested=command.visibility,
            project_visibility=project.visibility if project is not None else None,
            default=command.visibility,
        )
        before = _snapshot(work)
        updated = repository.update_work(
            self._session,
            org_id=self._org_id,
            work_id=work.id,
            expected_version=command.expected_version,
            visibility=command.visibility,
        )
        self._audit(
            action=Action.CHANGE_VISIBILITY,
            resource_type=ResourceType.WORK,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "WorkVisibilityChanged",
            "work",
            updated.id,
            {"from": before["visibility"], "to": updated.visibility},
        )
        return updated

    def change_status(self, command: ChangeWorkStatus) -> Work:
        work = self._load_work(command.work_id)
        project = authorization.work_project(self._session, work)
        decision = self._authorize(
            Action.CHANGE_STATE,
            ResourceType.WORK,
            authorization.work_authorship_relations(self._reach, work, project),
            work.id,
        )
        return self.apply_status(
            work,
            command.target,
            decision=decision,
            blocked_reason=command.blocked_reason,
            expected_version=command.expected_version,
        )

    def apply_status(
        self,
        work: Work,
        target: WorkStatus,
        *,
        decision: Decision,
        blocked_reason: str | None = None,
        actor: Actor | None = None,
        expected_version: int | None = None,
    ) -> Work:
        """The authorized status change, plus everything that follows from it."""
        current = WorkStatus(work.status)
        project = authorization.work_project(self._session, work)
        context = WorkTransitionContext(
            has_active_blocking_dependency=repository.has_active_blocking_dependency(
                self._session, org_id=self._org_id, work_id=work.id
            ),
            has_open_children=repository.has_open_children(
                self._session, org_id=self._org_id, work_id=work.id
            ),
            project_status=ProjectStatus(project.status) if project is not None else None,
            blocked_reason=blocked_reason,
        )
        validate_work_transition(current, target, context)

        now = dt.datetime.now(dt.UTC)
        before = _snapshot(work)
        values: dict[str, Any] = {
            "status": target.value,
            "completed_at": completed_at_for(target, now),
        }
        if target is WorkStatus.BLOCKED:
            values["blocked_reason"] = blocked_reason
        elif current is WorkStatus.BLOCKED:
            # Leaving `blocked` clears the cause, so a stale reason cannot outlive the block that
            # justified it and be read later as if it were current.
            values["blocked_reason"] = None
        if target is WorkStatus.IN_PROGRESS and work.started_at is None:
            values["started_at"] = now

        updated = repository.update_work(
            self._session,
            org_id=self._org_id,
            work_id=work.id,
            expected_version=(
                expected_version if expected_version is not None else work.version
            ),
            **values,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.WORK,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
            actor=actor,
        )
        self._emit(
            "WorkStatusChanged",
            "work",
            updated.id,
            {"from": current.value, "to": target.value},
            actor=actor,
        )
        if target is WorkStatus.DONE:
            # BR-W-08 records completion as its own fact, because "finished" is what rollups,
            # commitments and the timeline care about, not "changed status again".
            self._emit(
                "WorkCompleted",
                "work",
                updated.id,
                {"completed_at": _jsonable(updated.completed_at)},
                actor=actor,
            )
        _settle_dependencies(
            self,
            endpoint=Endpoint(type="work", id=updated.id),
            blocker_reached=target.value,
            decision=decision,
            actor=actor,
        )
        return updated


class AssignmentService(_WorkCoreService):
    def assign(self, command: AssignWork) -> WorkAssignment:
        work = self._load_work(command.work_id)
        project = authorization.work_project(self._session, work)
        # Creating an assignment is ASSIGN, including the first OWNER on unowned work. Taking
        # ownership *away from someone* is REASSIGN, and it cannot be smuggled through this call:
        # BR-W-13 refuses a second active OWNER, so the incumbent must be ended first, and ending
        # one is what `reassign_owner` does under the stronger permission.
        decision = self._authorize(
            Action.ASSIGN,
            ResourceType.WORK_ASSIGNMENT,
            authorization.assignment_creation_relations(
                self._reach, work, project, person_id=command.person_id
            ),
        )
        return self._create(work, command, decision=decision)

    def reassign_owner(self, command: ReassignOwner) -> WorkAssignment:
        """Move ownership: end the incumbent, then install the successor (BR-W-13, BR-W-14).

        One call rather than two because the single-active-OWNER index makes the intermediate state
        unrepresentable in the wrong order, and because "who owns this" should never be briefly
        nobody as a side effect of how the caller sequenced their requests.
        """
        work = self._load_work(command.work_id)
        project = authorization.work_project(self._session, work)
        decision = self._authorize(
            Action.REASSIGN,
            ResourceType.WORK_ASSIGNMENT,
            # No `person_id`: the authorship condition is for claiming work, never for moving it
            # between people, which is exactly what this call does.
            authorization.assignment_creation_relations(self._reach, work, project),
        )
        for assignment in repository.assignments_for_work(
            self._session, org_id=self._org_id, work_id=work.id
        ):
            if (
                AssignmentStatus(assignment.status) is AssignmentStatus.ACTIVE
                and AssignmentRole(assignment.role) is AssignmentRole.OWNER
            ):
                self._end(assignment, decision=decision)

        return self._create(
            work,
            AssignWork(
                work_id=command.work_id,
                person_id=command.person_id,
                role=AssignmentRole.OWNER,
                is_primary=command.is_primary,
                source=command.source,
            ),
            decision=decision,
        )

    def set_owner(self, command: SetOwner) -> WorkAssignment:
        """Put one person in the OWNER seat, in a single transaction.

        Which permission this needs depends on whether the seat is occupied, and that is a fact
        about the database rather than about the request — so the router cannot decide it and does
        not try. Unowned Work is `ASSIGN`: nobody is being displaced, and a member claiming Work
        they captured is the capture flow D1 exists for. Occupied Work is `REASSIGN`: somebody is
        being taken off their work, which is a decision about another person.

        The alternative — end, then assign — is the two-step route around `REASSIGN` that the
        Checkpoint 3.5 test guards against. Offering it as one authorized call is what makes closing
        that route reasonable rather than merely restrictive.
        """
        work = self._load_work(command.work_id)
        project = authorization.work_project(self._session, work)
        incumbent = next(
            (
                assignment
                for assignment in repository.assignments_for_work(
                    self._session, org_id=self._org_id, work_id=work.id
                )
                if AssignmentStatus(assignment.status) is AssignmentStatus.ACTIVE
                and AssignmentRole(assignment.role) is AssignmentRole.OWNER
            ),
            None,
        )

        if incumbent is None:
            decision = self._authorize(
                Action.ASSIGN,
                ResourceType.WORK_ASSIGNMENT,
                authorization.assignment_creation_relations(
                    self._reach, work, project, person_id=command.person_id
                ),
            )
        else:
            if incumbent.person_id == command.person_id:
                raise DomainRuleViolation(
                    "BR-W-13", "this person already holds the active OWNER assignment"
                )
            decision = self._authorize(
                Action.REASSIGN,
                ResourceType.WORK_ASSIGNMENT,
                authorization.assignment_creation_relations(self._reach, work, project),
            )
            self._end(incumbent, decision=decision)

        return self._create(
            work,
            AssignWork(
                work_id=command.work_id,
                person_id=command.person_id,
                role=AssignmentRole.OWNER,
                is_primary=command.is_primary,
                source=command.source,
            ),
            decision=decision,
        )

    def end(self, command: EndAssignment) -> WorkAssignment:
        assignment = self._load_assignment(command.assignment_id)
        work = self._load_work(assignment.work_id)
        project = authorization.work_project(self._session, work)
        decision = self._authorize(
            Action.END_ASSIGNMENT,
            ResourceType.WORK_ASSIGNMENT,
            authorization.assignment_relations(self._reach, work, project, assignment),
            assignment.id,
        )
        return self._end(assignment, decision=decision, expected_version=command.expected_version)

    # ------------------------------------------------------------------ internals

    def _create(
        self, work: Work, command: AssignWork, *, decision: Decision
    ) -> WorkAssignment:
        status = identity.person_status(
            self._session, org_id=self._org_id, person_id=command.person_id
        )
        if status is None:
            # BR-G-01, not a 404: the resource this request addressed is the Work item, and it is
            # right here. The thing that does not resolve is a field in the body, so the answer has
            # to name that field — a 404 would tell a client the work item had vanished (ADR-0035).
            raise DomainRuleViolation(
                "BR-G-01", "person_id does not name a person in this organization"
            )
        validate_assignment(
            person_id=command.person_id,
            role=command.role,
            is_primary=command.is_primary,
            person_status=status,
            existing=repository.existing_assignments(
                self._session, org_id=self._org_id, work_id=work.id
            ),
        )
        assignment = repository.insert_assignment(
            self._session,
            org_id=self._org_id,
            work_id=work.id,
            person_id=command.person_id,
            role=command.role.value,
            is_primary=command.is_primary,
            status=AssignmentStatus.ACTIVE.value,
            assigned_by_actor=self._ctx.actor.to_json(),
            source=command.source.value,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.ASSIGN,
            resource_type=ResourceType.WORK_ASSIGNMENT,
            resource_id=assignment.id,
            before=None,
            after=_snapshot(assignment),
            decision=decision,
        )
        self._emit(
            "WorkAssigned",
            "work_assignment",
            assignment.id,
            {
                "work_id": str(work.id),
                "person_id": str(assignment.person_id),
                "role": assignment.role,
            },
        )
        return assignment

    def _end(
        self,
        assignment: WorkAssignment,
        *,
        decision: Decision,
        expected_version: int | None = None,
    ) -> WorkAssignment:
        if AssignmentStatus(assignment.status) is AssignmentStatus.ENDED:
            raise DomainRuleViolation("BR-W-14", "this assignment has already ended")
        before = _snapshot(assignment)
        updated = repository.end_assignment(
            self._session,
            org_id=self._org_id,
            assignment_id=assignment.id,
            expected_version=(
                expected_version if expected_version is not None else assignment.version
            ),
            ended_at=dt.datetime.now(dt.UTC),
        )
        self._audit(
            action=Action.END_ASSIGNMENT,
            resource_type=ResourceType.WORK_ASSIGNMENT,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "WorkAssignmentEnded",
            "work_assignment",
            updated.id,
            {
                "work_id": str(updated.work_id),
                "person_id": str(updated.person_id),
                "role": updated.role,
            },
        )
        return updated


class DependencyService(_WorkCoreService):
    def create(self, command: CreateDependency) -> Dependency:
        blocker = Endpoint(type=command.blocker_type, id=command.blocker_id)
        blocked = Endpoint(type=command.blocked_type, id=command.blocked_id)
        for endpoint in (blocker, blocked):
            if not repository.endpoint_exists(
                self._session, org_id=self._org_id, endpoint=endpoint
            ):
                raise EntityNotFound(endpoint.type, endpoint.id)

        decision = self._authorize(
            Action.CREATE,
            ResourceType.DEPENDENCY,
            authorization.dependency_relations(
                self._session,
                self._reach,
                endpoints=((blocker.type, blocker.id), (blocked.type, blocked.id)),
            ),
        )
        candidate = DependencyEdge(blocker=blocker, blocked=blocked, kind=command.kind)
        validate_dependency(
            candidate=candidate,
            existing=repository.dependency_edges(self._session, org_id=self._org_id),
            # Both endpoints were just resolved inside this organization's RLS scope, so a
            # cross-organization edge is unreachable here. The domain check stays because the
            # domain is also called from places that have not proved that (BR-D-04).
            blocker_org=self._org_id,
            blocked_org=self._org_id,
        )
        dependency = repository.insert_dependency(
            self._session,
            org_id=self._org_id,
            blocker_type=blocker.type,
            blocker_id=blocker.id,
            blocked_type=blocked.type,
            blocked_id=blocked.id,
            kind=command.kind.value,
            status=DependencyStatus.ACTIVE.value,
            rationale=command.rationale,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.DEPENDENCY,
            resource_id=dependency.id,
            before=None,
            after=_snapshot(dependency),
            decision=decision,
        )
        self._emit(
            "DependencyCreated",
            "dependency",
            dependency.id,
            {
                "blocker": f"{blocker.type}:{blocker.id}",
                "blocked": f"{blocked.type}:{blocked.id}",
                "kind": dependency.kind,
            },
        )
        return dependency

    def change_status(self, command: ChangeDependencyStatus) -> Dependency:
        dependency = self._load_dependency(command.dependency_id)
        decision = self._authorize(
            Action.CHANGE_STATE,
            ResourceType.DEPENDENCY,
            authorization.dependency_relations(
                self._session,
                self._reach,
                endpoints=authorization.dependency_endpoints(dependency),
            ),
            dependency.id,
        )
        return self._apply_status(
            dependency,
            command.target,
            decision=decision,
            expected_version=command.expected_version,
        )

    def _apply_status(
        self,
        dependency: Dependency,
        target: DependencyStatus,
        *,
        decision: Decision,
        actor: Actor | None = None,
        expected_version: int | None = None,
    ) -> Dependency:
        current = DependencyStatus(dependency.status)
        if current is target:
            raise DomainRuleViolation("BR-D-06", f"dependency is already {target.value}")
        before = _snapshot(dependency)
        updated = repository.update_dependency_status(
            self._session,
            org_id=self._org_id,
            dependency_id=dependency.id,
            expected_version=(
                expected_version if expected_version is not None else dependency.version
            ),
            status=target,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.DEPENDENCY,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
            actor=actor,
        )
        self._emit(
            "DependencyStatusChanged",
            "dependency",
            updated.id,
            {"from": current.value, "to": target.value},
            actor=actor,
        )
        return updated


#: Statuses of a blocker that settle the dependencies hanging off it, and how (BR-D-03, BR-D-05).
_BLOCKER_SETTLEMENT: dict[str, DependencyStatus] = {
    WorkStatus.DONE.value: DependencyStatus.RESOLVED,
    MilestoneStatus.ACHIEVED.value: DependencyStatus.RESOLVED,
    WorkStatus.CANCELLED.value: DependencyStatus.WITHDRAWN,
    WorkStatus.REJECTED.value: DependencyStatus.WITHDRAWN,
    MilestoneStatus.CANCELLED.value: DependencyStatus.WITHDRAWN,
}


def _settle_dependencies(
    service: _WorkCoreService,
    *,
    endpoint: Endpoint,
    blocker_reached: str,
    decision: Decision,
    actor: Actor | None,
) -> None:
    """BR-D-03 and BR-D-05: a blocker reaching a terminal state settles what hangs off it.

    What this deliberately does **not** do is move the blocked item. BR-D-03 says the resolution
    "unblocks the blocked item", and the narrow reading is the safe one: the blocked work becomes
    *free to move*, because `has_active_blocking_dependency` is now false for it, rather than being
    moved by the system. Status is a business field a human sets, and writing one on somebody
    else's work as a side effect of finishing your own is the kind of automatic edit BR-AI-29 rules
    out. Recorded in `progress.md` as the reading taken.
    """
    target = _BLOCKER_SETTLEMENT.get(blocker_reached)
    if target is None:
        return
    dependencies = DependencyService(service._ctx)
    for dependency in repository.active_dependencies_from(
        service._session, org_id=service._org_id, blocker=endpoint
    ):
        if DependencyKind(dependency.kind) is not DependencyKind.BLOCKS:
            continue
        dependencies._apply_status(dependency, target, decision=decision, actor=actor)
