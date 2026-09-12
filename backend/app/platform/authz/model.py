"""The vocabulary of authorization.

Kept deliberately small. Adding a Role, ResourceType or Action here fails the matrix coverage test
until the corresponding cells are declared, which is the point: the matrix is the spec, and the
spec is not allowed to have holes.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field


class Role(enum.StrEnum):
    ORG_ADMIN = "org_admin"
    DEPARTMENT_LEAD = "department_lead"
    TEAM_LEAD = "team_lead"
    MEMBER = "member"
    VIEWER = "viewer"
    AUDITOR = "auditor"
    EXECUTIVE = "executive"


class ResourceType(enum.StrEnum):
    ORGANIZATION = "organization"
    DEPARTMENT = "department"
    TEAM = "team"
    PERSON = "person"
    ORGANIZATION_MEMBERSHIP = "organization_membership"
    ROLE_ASSIGNMENT = "role_assignment"
    EXTERNAL_IDENTITY = "external_identity"
    PROJECT = "project"
    MILESTONE = "milestone"
    WORK = "work"
    WORK_ASSIGNMENT = "work_assignment"
    DEPENDENCY = "dependency"
    EVENT = "event"
    EVIDENCE = "evidence"
    COMMITMENT = "commitment"
    PROPOSAL = "proposal"
    AUDIT_ENTRY = "audit_entry"


class Action(enum.StrEnum):
    CREATE = "create"
    READ = "read"
    LIST = "list"
    UPDATE = "update"
    CHANGE_STATE = "change_state"
    CHANGE_VISIBILITY = "change_visibility"
    ASSIGN = "assign"
    REASSIGN = "reassign"
    END_ASSIGNMENT = "end_assignment"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_ROLES = "manage_roles"
    #: Attaching a file to an Event. Not a resource of its own: ADR-0039 makes attachment
    #: authorization the Event's authorization, so this action is declared on EVENT.
    ATTACH = "attach"
    #: Deciding a Proposal. Separate from CHANGE_STATE because approving is not editing a
    #: status field — it delegates the approver's own authority to an execution (BR-PR-05).
    APPROVE = "approve"
    #: BR-E-06. Evidence is never edited; a correction is a new row the old one points to.
    SUPERSEDE = "supersede"


class Grant(enum.StrEnum):
    """What a role's permission is conditional on."""

    DENY = "deny"           # never, regardless of relationship
    ORG = "org"             # any resource in the actor's organization
    DEPARTMENT = "dept"     # resource inside the actor's department subtree
    TEAM = "team"           # resource owned by a team the actor belongs to
    PERSONAL = "personal"   # actor has a direct relationship to the resource
    SELF = "self"           # the resource is the actor's own record


class Relation(enum.StrEnum):
    """Facts about the actor's relationship to a resource, supplied by the calling service."""

    SAME_ORG = "same_org"
    IN_DEPARTMENT = "in_department"
    IN_TEAM = "in_team"
    PERSONAL = "personal"
    SELF = "self"


GRANT_REQUIRES: dict[Grant, Relation | None] = {
    Grant.ORG: Relation.SAME_ORG,
    Grant.DEPARTMENT: Relation.IN_DEPARTMENT,
    Grant.TEAM: Relation.IN_TEAM,
    Grant.PERSONAL: Relation.PERSONAL,
    Grant.SELF: Relation.SELF,
    Grant.DENY: None,
}


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated actor as far as authorization is concerned."""

    person_id: uuid.UUID
    org_id: uuid.UUID
    roles: frozenset[Role]

    def __post_init__(self) -> None:
        if not self.roles:
            raise ValueError("a principal must hold at least one role")


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """The resource being acted on, plus the actor's relationship to it.

    Relations are computed by the application service that owns the resource, because only it
    knows what "in my team" means for that entity. The policy engine never queries the database.
    """

    type: ResourceType
    org_id: uuid.UUID
    id: uuid.UUID | None = None
    relations: frozenset[Relation] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    matched_role: Role | None = None
    matched_grant: Grant | None = None

    def __bool__(self) -> bool:
        return self.allowed

    def to_json(self) -> dict[str, str | bool | None]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "role": self.matched_role.value if self.matched_role else None,
            "grant": self.matched_grant.value if self.matched_grant else None,
        }


class AuthorizationError(PermissionError):
    """Raised by `authorize`. Carries the decision so it can be audited and logged."""

    def __init__(self, decision: Decision, action: Action, resource: ResourceRef) -> None:
        super().__init__(f"{action.value} denied on {resource.type.value}: {decision.reason}")
        self.decision = decision
        self.action = action
        self.resource = resource
