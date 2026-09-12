"""Identity & Organization application services.

One use case per method, each walking the same five steps in the same order:

    authorize → validate in the domain → write through the repository → audit → emit

Identity writes are the changes an organization cares most about being able to explain afterwards —
who added this person, who granted this role, who decided that phone number belongs to that human —
so the audit entry is not a by-product here, it is most of the value.

Transactions belong to the caller, as in the Work Core: the session arrives from an `org_session`
and several calls compose into one atomic unit of work.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Session

from app.contexts.identity import authorization, repository
from app.contexts.identity.commands import (
    AddTeamMember,
    ChangeMembershipStatus,
    ChangePersonStatus,
    ChangeUnitStatus,
    ConfirmExternalIdentity,
    CreateDepartment,
    CreateExternalIdentity,
    CreateMembership,
    CreatePerson,
    CreateTeam,
    EndTeamMembership,
    GrantRole,
    RevokeRole,
    UpdateDepartment,
    UpdateExternalIdentity,
    UpdateOrganization,
    UpdatePerson,
    UpdateTeam,
)
from app.contexts.identity.domain import (
    MembershipStatus,
    PersonStatus,
    ScopeType,
    UnitStatus,
    validate_department_placement,
    validate_external_identity,
    validate_membership_transition,
    validate_organization_membership,
    validate_organization_update,
    validate_person_creation,
    validate_person_transition,
    validate_role_assignment,
    validate_team_membership,
    validate_unit_name,
)
from app.contexts.identity.models import (
    Department,
    ExternalIdentity,
    Organization,
    OrganizationMembership,
    Person,
    RoleAssignment,
    Team,
    TeamMembership,
)
from app.platform.actor import Actor, ActorType
from app.platform.audit import record_audit
from app.platform.authz import (
    Action,
    Decision,
    Principal,
    Relation,
    ResourceRef,
    ResourceType,
    Role,
    authorize,
)
from app.platform.errors import DomainRuleViolation, EntityNotFound
from app.platform.outbox import append_domain_event
from app.platform.partial import changed_fields


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def _snapshot(entity: DeclarativeBase) -> dict[str, Any]:
    mapper = sa_inspect(type(entity))
    return {attr.key: _jsonable(getattr(entity, attr.key)) for attr in mapper.column_attrs}


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceContext:
    session: Session
    principal: Principal
    actor: Actor

    def __post_init__(self) -> None:
        if self.actor.type is ActorType.SYSTEM:
            return
        if self.actor.person_id != self.principal.person_id:
            raise ValueError(
                "the actor and the authorizing principal must be the same person (BR-AI-03)"
            )


class _IdentityService:
    def __init__(self, context: ServiceContext) -> None:
        self._ctx = context
        self._reach = authorization.reach_of(context.session, context.principal)

    @property
    def _session(self) -> Session:
        return self._ctx.session

    @property
    def _org_id(self) -> uuid.UUID:
        return self._ctx.principal.org_id

    @property
    def _created_by(self) -> uuid.UUID | None:
        return self._ctx.actor.person_id

    def _authorize(
        self,
        action: Action,
        resource_type: ResourceType,
        relations: frozenset[Relation] = frozenset(),
        resource_id: uuid.UUID | None = None,
    ) -> Decision:
        return authorize(
            self._ctx.principal,
            action,
            ResourceRef(
                type=resource_type, org_id=self._org_id, id=resource_id, relations=relations
            ),
        )

    def _audit(
        self,
        *,
        action: Action,
        resource_type: ResourceType,
        resource_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        decision: Decision,
    ) -> None:
        record_audit(
            self._session,
            org_id=self._org_id,
            actor=self._ctx.actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
            decision=decision,
        )

    def _emit(
        self, event_type: str, aggregate_type: str, aggregate_id: uuid.UUID, payload: dict[str, Any]
    ) -> None:
        append_domain_event(
            self._session,
            org_id=self._org_id,
            type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            actor=self._ctx.actor,
        )

    # ------------------------------------------------------------------ loaders

    def _load_person(self, person_id: uuid.UUID) -> Person:
        person = repository.get_person(self._session, org_id=self._org_id, person_id=person_id)
        if person is None:
            raise EntityNotFound("person", person_id)
        return person

    def _load_department(self, department_id: uuid.UUID) -> Department:
        department = repository.get_department(
            self._session, org_id=self._org_id, department_id=department_id
        )
        if department is None:
            raise EntityNotFound("department", department_id)
        return department

    def _load_team(self, team_id: uuid.UUID) -> Team:
        team = repository.get_team(self._session, org_id=self._org_id, team_id=team_id)
        if team is None:
            raise EntityNotFound("team", team_id)
        return team


class OrganizationService(_IdentityService):
    """Updates only. Creating a tenant is an administrative act outside every tenant (ADR-0036)."""

    def update(self, command: UpdateOrganization) -> Organization:
        organization = repository.get_organization(self._session, org_id=self._org_id)
        if organization is None:
            raise EntityNotFound("organization", self._org_id)
        decision = self._authorize(Action.UPDATE, ResourceType.ORGANIZATION, frozenset())
        before = _snapshot(organization)
        changes = changed_fields(command)
        changes.pop("expected_version")
        validate_organization_update(
            name=str(changes.get("name", organization.name)),
            timezone=str(changes.get("timezone", organization.timezone)),
        )
        updated = repository.update_organization(
            self._session,
            org_id=self._org_id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.ORGANIZATION,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit("OrganizationUpdated", "organization", updated.id, {"changed": sorted(changes)})
        return updated


class PersonService(_IdentityService):
    def create(self, command: CreatePerson) -> Person:
        """ADR-0036. A Person exists because somebody with PERSON.CREATE made one."""
        decision = self._authorize(Action.CREATE, ResourceType.PERSON)
        status = validate_person_creation(
            display_name=command.display_name, email=command.email
        )
        if command.keycloak_subject is not None:
            existing = repository.person_by_subject(
                self._session, org_id=self._org_id, subject=command.keycloak_subject
            )
            if existing is not None:
                raise DomainRuleViolation(
                    "BR-I-04",
                    "that sign-in subject already belongs to somebody in this organization",
                )
        person = repository.insert_person(
            self._session,
            org_id=self._org_id,
            display_name=command.display_name,
            email=command.email,
            keycloak_subject=command.keycloak_subject,
            timezone=command.timezone,
            status=status.value,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.PERSON,
            resource_id=person.id,
            before=None,
            after=_snapshot(person),
            decision=decision,
        )
        self._emit(
            "PersonCreated",
            "person",
            person.id,
            {
                "display_name": person.display_name,
                "can_sign_in": person.keycloak_subject is not None,
            },
        )
        return person

    def update(self, command: UpdatePerson) -> Person:
        person = self._load_person(command.person_id)
        decision = self._authorize(
            Action.UPDATE,
            ResourceType.PERSON,
            authorization.person_relations(self._session, self._reach, person.id),
            person.id,
        )
        before = _snapshot(person)
        changes = changed_fields(command)
        changes.pop("person_id")
        changes.pop("expected_version")
        validate_person_creation(
            display_name=str(changes.get("display_name", person.display_name)),
            email=changes.get("email", person.email),
        )
        subject = changes.get("keycloak_subject")
        if subject is not None:
            clash = repository.person_by_subject(
                self._session, org_id=self._org_id, subject=str(subject)
            )
            if clash is not None and clash.id != person.id:
                raise DomainRuleViolation(
                    "BR-I-04",
                    "that sign-in subject already belongs to somebody in this organization",
                )
        updated = repository.update_person(
            self._session,
            org_id=self._org_id,
            person_id=person.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.PERSON,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit("PersonUpdated", "person", updated.id, {"changed": sorted(changes)})
        return updated

    def change_status(self, command: ChangePersonStatus) -> Person:
        """BR-I-05. Departure ends nothing by itself; it stops new work arriving.

        Existing assignments are retained and ended deliberately, which is the rule's own wording.
        Cascading them from here would destroy the history the rule is protecting.
        """
        person = self._load_person(command.person_id)
        decision = self._authorize(
            Action.CHANGE_STATE,
            ResourceType.PERSON,
            authorization.person_relations(self._session, self._reach, person.id),
            person.id,
        )
        validate_person_transition(PersonStatus(person.status), command.target)
        before = _snapshot(person)
        updated = repository.update_person(
            self._session,
            org_id=self._org_id,
            person_id=person.id,
            expected_version=command.expected_version,
            status=command.target.value,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.PERSON,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "PersonStatusChanged",
            "person",
            updated.id,
            {"from": before["status"], "to": updated.status},
        )
        return updated


class DepartmentService(_IdentityService):
    def create(self, command: CreateDepartment) -> Department:
        decision = self._authorize(Action.CREATE, ResourceType.DEPARTMENT)
        validate_unit_name("BR-I-01", command.name, "department")
        ancestors: list[uuid.UUID] = []
        if command.parent_department_id is not None:
            parent = self._load_department(command.parent_department_id)
            ancestors = repository.department_ancestors(
                self._session, org_id=self._org_id, department_id=parent.id
            )
        validate_department_placement(
            department_id=None, parent_id=command.parent_department_id, ancestors=ancestors
        )
        if command.lead_person_id is not None:
            self._load_person(command.lead_person_id)

        department = repository.insert_department(
            self._session,
            org_id=self._org_id,
            name=command.name,
            parent_department_id=command.parent_department_id,
            lead_person_id=command.lead_person_id,
            status=UnitStatus.ACTIVE.value,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.DEPARTMENT,
            resource_id=department.id,
            before=None,
            after=_snapshot(department),
            decision=decision,
        )
        self._emit("DepartmentCreated", "department", department.id, {"name": department.name})
        return department

    def update(self, command: UpdateDepartment) -> Department:
        department = self._load_department(command.department_id)
        decision = self._authorize(
            Action.UPDATE,
            ResourceType.DEPARTMENT,
            authorization.department_relations(self._reach, department.id),
            department.id,
        )
        before = _snapshot(department)
        changes = changed_fields(command)
        changes.pop("department_id")
        changes.pop("expected_version")
        if "name" in changes:
            validate_unit_name("BR-I-01", str(changes["name"]), "department")
        if "parent_department_id" in changes and changes["parent_department_id"] is not None:
            parent_id = changes["parent_department_id"]
            self._load_department(parent_id)
            validate_department_placement(
                department_id=department.id,
                parent_id=parent_id,
                ancestors=repository.department_ancestors(
                    self._session, org_id=self._org_id, department_id=parent_id
                ),
            )
        if changes.get("lead_person_id") is not None:
            self._load_person(changes["lead_person_id"])

        updated = repository.update_department(
            self._session,
            org_id=self._org_id,
            department_id=department.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.DEPARTMENT,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit("DepartmentUpdated", "department", updated.id, {"changed": sorted(changes)})
        return updated

    def change_status(self, command: ChangeUnitStatus) -> Department:
        department = self._load_department(command.entity_id)
        decision = self._authorize(
            Action.CHANGE_STATE,
            ResourceType.DEPARTMENT,
            authorization.department_relations(self._reach, department.id),
            department.id,
        )
        if department.status == command.target.value:
            raise DomainRuleViolation(
                "BR-G-04", f"this department is already {command.target.value}"
            )
        before = _snapshot(department)
        updated = repository.update_department(
            self._session,
            org_id=self._org_id,
            department_id=department.id,
            expected_version=command.expected_version,
            status=command.target.value,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.DEPARTMENT,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "DepartmentStatusChanged",
            "department",
            updated.id,
            {"from": before["status"], "to": updated.status},
        )
        return updated


class TeamService(_IdentityService):
    def create(self, command: CreateTeam) -> Team:
        relations: frozenset[Relation] = frozenset()
        if command.department_id is not None:
            self._load_department(command.department_id)
            relations = authorization.department_relations(self._reach, command.department_id)
        decision = self._authorize(Action.CREATE, ResourceType.TEAM, relations)
        validate_unit_name("BR-I-02", command.name, "team")
        if command.lead_person_id is not None:
            self._load_person(command.lead_person_id)

        team = repository.insert_team(
            self._session,
            org_id=self._org_id,
            name=command.name,
            department_id=command.department_id,
            lead_person_id=command.lead_person_id,
            status=UnitStatus.ACTIVE.value,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.TEAM,
            resource_id=team.id,
            before=None,
            after=_snapshot(team),
            decision=decision,
        )
        self._emit("TeamCreated", "team", team.id, {"name": team.name})
        return team

    def update(self, command: UpdateTeam) -> Team:
        team = self._load_team(command.team_id)
        decision = self._authorize(
            Action.UPDATE,
            ResourceType.TEAM,
            authorization.team_relations(
                self._reach, team_id=team.id, department_id=team.department_id
            ),
            team.id,
        )
        before = _snapshot(team)
        changes = changed_fields(command)
        changes.pop("team_id")
        changes.pop("expected_version")
        if "name" in changes:
            validate_unit_name("BR-I-02", str(changes["name"]), "team")
        if changes.get("department_id") is not None:
            self._load_department(changes["department_id"])
        if changes.get("lead_person_id") is not None:
            self._load_person(changes["lead_person_id"])

        updated = repository.update_team(
            self._session,
            org_id=self._org_id,
            team_id=team.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.TEAM,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit("TeamUpdated", "team", updated.id, {"changed": sorted(changes)})
        return updated

    def change_status(self, command: ChangeUnitStatus) -> Team:
        team = self._load_team(command.entity_id)
        decision = self._authorize(
            Action.CHANGE_STATE,
            ResourceType.TEAM,
            authorization.team_relations(
                self._reach, team_id=team.id, department_id=team.department_id
            ),
            team.id,
        )
        if team.status == command.target.value:
            raise DomainRuleViolation("BR-G-04", f"this team is already {command.target.value}")
        before = _snapshot(team)
        updated = repository.update_team(
            self._session,
            org_id=self._org_id,
            team_id=team.id,
            expected_version=command.expected_version,
            status=command.target.value,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.TEAM,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "TeamStatusChanged",
            "team",
            updated.id,
            {"from": before["status"], "to": updated.status},
        )
        return updated

    def add_member(self, command: AddTeamMember) -> TeamMembership:
        team = self._load_team(command.team_id)
        decision = self._authorize(
            Action.MANAGE_MEMBERS,
            ResourceType.TEAM,
            authorization.team_relations(
                self._reach, team_id=team.id, department_id=team.department_id
            ),
            team.id,
        )
        person = self._load_person(command.person_id)
        validate_team_membership(
            person_id=person.id,
            person_status=PersonStatus(person.status),
            existing=repository.existing_team_memberships(
                self._session, org_id=self._org_id, team_id=team.id
            ),
        )
        membership = repository.insert_team_membership(
            self._session,
            org_id=self._org_id,
            team_id=team.id,
            person_id=person.id,
            role=command.role.value,
            valid_from=command.valid_from or dt.datetime.now(dt.UTC),
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.MANAGE_MEMBERS,
            resource_type=ResourceType.TEAM,
            resource_id=team.id,
            before=None,
            after=_snapshot(membership),
            decision=decision,
        )
        self._emit(
            "TeamMemberAdded",
            "team_membership",
            membership.id,
            {"team_id": str(team.id), "person_id": str(person.id), "role": membership.role},
        )
        return membership

    def end_membership(self, command: EndTeamMembership) -> TeamMembership:
        """BR-I-03. Sets `valid_to`; the row survives so history stays queryable."""
        membership = repository.get_team_membership(
            self._session, org_id=self._org_id, membership_id=command.membership_id
        )
        if membership is None:
            raise EntityNotFound("team_membership", command.membership_id)
        team = self._load_team(membership.team_id)
        decision = self._authorize(
            Action.MANAGE_MEMBERS,
            ResourceType.TEAM,
            authorization.team_relations(
                self._reach, team_id=team.id, department_id=team.department_id
            ),
            team.id,
        )
        if membership.valid_to is not None:
            raise DomainRuleViolation("BR-I-03", "this membership has already ended")
        before = _snapshot(membership)
        updated = repository.end_team_membership(
            self._session,
            org_id=self._org_id,
            membership_id=membership.id,
            expected_version=command.expected_version,
            valid_to=dt.datetime.now(dt.UTC),
        )
        self._audit(
            action=Action.MANAGE_MEMBERS,
            resource_type=ResourceType.TEAM,
            resource_id=team.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "TeamMemberRemoved",
            "team_membership",
            updated.id,
            {"team_id": str(team.id), "person_id": str(updated.person_id)},
        )
        return updated


class MembershipService(_IdentityService):
    """Membership of the organization itself — what `resolve_principal` requires to exist."""

    def create(self, command: CreateMembership) -> OrganizationMembership:
        decision = self._authorize(Action.CREATE, ResourceType.ORGANIZATION_MEMBERSHIP)
        person = self._load_person(command.person_id)
        existing = repository.membership_for_person(
            self._session, org_id=self._org_id, person_id=person.id
        )
        validate_organization_membership(
            person_status=PersonStatus(person.status),
            existing_status=MembershipStatus(existing.status) if existing else None,
        )
        membership = repository.insert_organization_membership(
            self._session,
            org_id=self._org_id,
            person_id=person.id,
            status=MembershipStatus.ACTIVE.value,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.ORGANIZATION_MEMBERSHIP,
            resource_id=membership.id,
            before=None,
            after=_snapshot(membership),
            decision=decision,
        )
        self._emit(
            "MembershipCreated",
            "organization_membership",
            membership.id,
            {"person_id": str(person.id)},
        )
        return membership

    def change_status(self, command: ChangeMembershipStatus) -> OrganizationMembership:
        membership = repository.get_organization_membership(
            self._session, org_id=self._org_id, membership_id=command.membership_id
        )
        if membership is None:
            raise EntityNotFound("organization_membership", command.membership_id)
        decision = self._authorize(
            Action.CHANGE_STATE, ResourceType.ORGANIZATION_MEMBERSHIP, frozenset(), membership.id
        )
        target = command.target
        validate_membership_transition(MembershipStatus(membership.status), target)
        before = _snapshot(membership)
        values: dict[str, Any] = {"status": target.value}
        values["left_at"] = (
            dt.datetime.now(dt.UTC) if target is MembershipStatus.ENDED else None
        )
        updated = repository.update_organization_membership(
            self._session,
            org_id=self._org_id,
            membership_id=membership.id,
            expected_version=command.expected_version,
            **values,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.ORGANIZATION_MEMBERSHIP,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "MembershipStatusChanged",
            "organization_membership",
            updated.id,
            {"from": before["status"], "to": updated.status},
        )
        return updated


class RoleService(_IdentityService):
    """Granting and revoking. `org_admin` only — there is no path for a role to grant itself."""

    def grant(self, command: GrantRole) -> RoleAssignment:
        decision = self._authorize(Action.CREATE, ResourceType.ROLE_ASSIGNMENT)
        person = self._load_person(command.person_id)
        if command.role not in {role.value for role in Role}:
            raise DomainRuleViolation("BR-G-01", f"{command.role} is not a role in this system")
        if command.scope_type is ScopeType.DEPARTMENT and command.scope_id is not None:
            self._load_department(command.scope_id)
        if command.scope_type is ScopeType.TEAM and command.scope_id is not None:
            self._load_team(command.scope_id)
        validate_role_assignment(
            role=command.role,
            scope_type=command.scope_type,
            scope_id=command.scope_id,
            person_status=PersonStatus(person.status),
            existing=repository.existing_role_assignments(
                self._session, org_id=self._org_id, person_id=person.id
            ),
        )
        grant = repository.insert_role_assignment(
            self._session,
            org_id=self._org_id,
            person_id=person.id,
            role=command.role,
            scope_type=command.scope_type.value,
            scope_id=command.scope_id,
            granted_by_person_id=self._created_by,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.ROLE_ASSIGNMENT,
            resource_id=grant.id,
            before=None,
            after=_snapshot(grant),
            decision=decision,
        )
        self._emit(
            "RoleGranted",
            "role_assignment",
            grant.id,
            {"person_id": str(person.id), "role": grant.role, "scope": grant.scope_type},
        )
        return grant

    def revoke(self, command: RevokeRole) -> RoleAssignment:
        grant = repository.get_role_assignment(
            self._session, org_id=self._org_id, assignment_id=command.assignment_id
        )
        if grant is None:
            raise EntityNotFound("role_assignment", command.assignment_id)
        decision = self._authorize(
            Action.CHANGE_STATE, ResourceType.ROLE_ASSIGNMENT, frozenset(), grant.id
        )
        if grant.revoked_at is not None:
            raise DomainRuleViolation("BR-G-01", "this role has already been revoked")
        before = _snapshot(grant)
        updated = repository.revoke_role_assignment(
            self._session,
            org_id=self._org_id,
            assignment_id=grant.id,
            expected_version=command.expected_version,
            revoked_at=dt.datetime.now(dt.UTC),
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.ROLE_ASSIGNMENT,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "RoleRevoked",
            "role_assignment",
            updated.id,
            {"person_id": str(updated.person_id), "role": updated.role},
        )
        return updated


class ExternalIdentityService(_IdentityService):
    """ADR-0037. Writes are `org_admin` only, and confirmation is never self-service."""

    def create(self, command: CreateExternalIdentity) -> ExternalIdentity:
        decision = self._authorize(Action.CREATE, ResourceType.EXTERNAL_IDENTITY)
        person = self._load_person(command.person_id)
        validate_external_identity(
            source_system=command.source_system,
            external_id=command.external_id,
            confidence=command.confidence,
            existing=repository.existing_external_identities(self._session, org_id=self._org_id),
        )
        mapping = repository.insert_external_identity(
            self._session,
            org_id=self._org_id,
            person_id=person.id,
            source_system=command.source_system,
            external_id=command.external_id,
            handle=command.handle,
            confidence=command.confidence,
            created_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CREATE,
            resource_type=ResourceType.EXTERNAL_IDENTITY,
            resource_id=mapping.id,
            before=None,
            after=_snapshot(mapping),
            decision=decision,
        )
        self._emit(
            "ExternalIdentityCreated",
            "external_identity",
            mapping.id,
            {
                "person_id": str(person.id),
                "source_system": mapping.source_system,
                # Never the external id itself: a phone number in an event payload is the payload
                # travelling somewhere the row's own access rules do not follow.
                "confirmed": mapping.confirmed_at is not None,
            },
        )
        return mapping

    def update(self, command: UpdateExternalIdentity) -> ExternalIdentity:
        mapping = self._load_mapping(command.identity_id)
        decision = self._authorize(
            Action.UPDATE, ResourceType.EXTERNAL_IDENTITY, frozenset(), mapping.id
        )
        before = _snapshot(mapping)
        changes = changed_fields(command)
        changes.pop("identity_id")
        changes.pop("expected_version")
        if "confidence" in changes:
            confidence = int(changes["confidence"])
            if not 0 <= confidence <= 100:
                raise DomainRuleViolation("BR-I-06", "confidence is a percentage from 0 to 100")
        updated = repository.update_external_identity(
            self._session,
            org_id=self._org_id,
            identity_id=mapping.id,
            expected_version=command.expected_version,
            **changes,
        )
        self._audit(
            action=Action.UPDATE,
            resource_type=ResourceType.EXTERNAL_IDENTITY,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "ExternalIdentityUpdated",
            "external_identity",
            updated.id,
            {"changed": sorted(changes)},
        )
        return updated

    def confirm(self, command: ConfirmExternalIdentity) -> ExternalIdentity:
        """Records who confirmed it and when — the provenance BR-I-06 rests on."""
        mapping = self._load_mapping(command.identity_id)
        decision = self._authorize(
            Action.CHANGE_STATE, ResourceType.EXTERNAL_IDENTITY, frozenset(), mapping.id
        )
        if mapping.confirmed_at is not None:
            raise DomainRuleViolation("BR-I-06", "this mapping has already been confirmed")
        if self._created_by is None:
            raise DomainRuleViolation(
                "BR-I-06", "a confirmation has to name the person who made it"
            )
        before = _snapshot(mapping)
        updated = repository.update_external_identity(
            self._session,
            org_id=self._org_id,
            identity_id=mapping.id,
            expected_version=command.expected_version,
            confirmed_at=dt.datetime.now(dt.UTC),
            confirmed_by_person_id=self._created_by,
        )
        self._audit(
            action=Action.CHANGE_STATE,
            resource_type=ResourceType.EXTERNAL_IDENTITY,
            resource_id=updated.id,
            before=before,
            after=_snapshot(updated),
            decision=decision,
        )
        self._emit(
            "ExternalIdentityConfirmed",
            "external_identity",
            updated.id,
            {"person_id": str(updated.person_id), "confidence": updated.confidence},
        )
        return updated

    def _load_mapping(self, identity_id: uuid.UUID) -> ExternalIdentity:
        mapping = repository.get_external_identity(
            self._session, org_id=self._org_id, identity_id=identity_id
        )
        if mapping is None:
            raise EntityNotFound("external_identity", identity_id)
        return mapping
