"""The authorization matrix.

This file is data, not logic. It is the specification of who may do what, and it is reviewed as a
spec. `tests/unit/test_authz_matrix.py` asserts that every applicable (resource, action) pair has a
declaration for every role, so adding a role, resource or action breaks the build until the new
cells are filled in deliberately.

Column order in every row is fixed:

    admin, dept_lead, team_lead, member, viewer, auditor, executive

Grants are conditions, not permissions in themselves. `TEAM` means "allowed when the actor is
related to this resource through a team they belong to". The relationship facts are supplied by the
application service that owns the resource; the policy engine never goes to the database.

Two decisions worth noticing, both from the Phase 1 readiness review:

* `WORK.CREATE` gives `member` an `ORG` grant, not `TEAM`. Work with no Project and no team context
  must be creatable by any member, otherwise capture of "someone should check the IOC API" has
  nowhere to land (BR-W-07, BR-W-15).
* `WORK_ASSIGNMENT.REASSIGN` denies `member` even though `WORK.UPDATE` allows `PERSONAL`. Editing
  work you own and changing who owns it are different powers (ADR-0032).
"""

from __future__ import annotations

from app.platform.authz.model import Action, Grant, ResourceType, Role

_ROLE_ORDER: tuple[Role, ...] = (
    Role.ORG_ADMIN,
    Role.DEPARTMENT_LEAD,
    Role.TEAM_LEAD,
    Role.MEMBER,
    Role.VIEWER,
    Role.AUDITOR,
    Role.EXECUTIVE,
)

# Short aliases so the table below stays readable.
NO = (Grant.DENY,)
ORG = (Grant.ORG,)
DEPT = (Grant.DEPARTMENT,)
TEAM = (Grant.TEAM,)
OWN = (Grant.PERSONAL,)
SELF = (Grant.SELF,)
TEAM_OR_OWN = (Grant.TEAM, Grant.PERSONAL)

Cell = tuple[Grant, ...]


def row(
    admin: Cell,
    dept_lead: Cell,
    team_lead: Cell,
    member: Cell,
    viewer: Cell,
    auditor: Cell,
    executive: Cell,
) -> dict[Role, frozenset[Grant]]:
    values = (admin, dept_lead, team_lead, member, viewer, auditor, executive)
    return {role: frozenset(grants) for role, grants in zip(_ROLE_ORDER, values, strict=True)}


R = ResourceType
A = Action

#: Which actions are meaningful for which resource. The matrix must cover exactly these pairs.
RESOURCE_ACTIONS: dict[ResourceType, frozenset[Action]] = {
    R.ORGANIZATION: frozenset({A.READ, A.UPDATE, A.MANAGE_MEMBERS, A.MANAGE_ROLES}),
    R.DEPARTMENT: frozenset({A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE}),
    R.TEAM: frozenset(
        {A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE, A.MANAGE_MEMBERS}
    ),
    R.PERSON: frozenset({A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE}),
    R.ORGANIZATION_MEMBERSHIP: frozenset(
        {A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE}
    ),
    R.ROLE_ASSIGNMENT: frozenset({A.CREATE, A.READ, A.LIST, A.CHANGE_STATE}),
    R.EXTERNAL_IDENTITY: frozenset({A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE}),
    R.PROJECT: frozenset(
        {A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE, A.CHANGE_VISIBILITY}
    ),
    R.MILESTONE: frozenset({A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE}),
    R.WORK: frozenset(
        {A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE, A.CHANGE_VISIBILITY}
    ),
    R.WORK_ASSIGNMENT: frozenset(
        {A.READ, A.LIST, A.ASSIGN, A.REASSIGN, A.END_ASSIGNMENT}
    ),
    R.DEPENDENCY: frozenset({A.CREATE, A.READ, A.LIST, A.CHANGE_STATE}),
    R.AUDIT_ENTRY: frozenset({A.READ, A.LIST}),
}

MATRIX: dict[tuple[ResourceType, Action], dict[Role, frozenset[Grant]]] = {
    # ---------------------------------------------------------------- organization
    #                                 admin  dept  team  member viewer audit exec
    (R.ORGANIZATION, A.READ):        row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.ORGANIZATION, A.UPDATE):      row(ORG,  NO,   NO,   NO,    NO,    NO,   NO),
    (R.ORGANIZATION, A.MANAGE_MEMBERS): row(ORG, NO,  NO,   NO,    NO,    NO,   NO),
    (R.ORGANIZATION, A.MANAGE_ROLES):   row(ORG, NO,  NO,   NO,    NO,    NO,   NO),
    # ---------------------------------------------------------------- department
    (R.DEPARTMENT, A.CREATE):        row(ORG,  NO,   NO,   NO,    NO,    NO,   NO),
    (R.DEPARTMENT, A.READ):          row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.DEPARTMENT, A.LIST):          row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.DEPARTMENT, A.UPDATE):        row(ORG,  DEPT, NO,   NO,    NO,    NO,   NO),
    (R.DEPARTMENT, A.CHANGE_STATE):  row(ORG,  DEPT, NO,   NO,    NO,    NO,   NO),
    # ---------------------------------------------------------------- team
    (R.TEAM, A.CREATE):              row(ORG,  DEPT, NO,   NO,    NO,    NO,   NO),
    (R.TEAM, A.READ):                row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.TEAM, A.LIST):                row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.TEAM, A.UPDATE):              row(ORG,  DEPT, TEAM, NO,    NO,    NO,   NO),
    (R.TEAM, A.CHANGE_STATE):        row(ORG,  DEPT, TEAM, NO,    NO,    NO,   NO),
    (R.TEAM, A.MANAGE_MEMBERS):      row(ORG,  DEPT, TEAM, NO,    NO,    NO,   NO),
    # ---------------------------------------------------------------- person
    (R.PERSON, A.CREATE):            row(ORG,  NO,   NO,   NO,    NO,    NO,   NO),
    (R.PERSON, A.READ):              row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.PERSON, A.LIST):              row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.PERSON, A.UPDATE):            row(ORG,  DEPT, NO,   SELF,  NO,    NO,   NO),
    (R.PERSON, A.CHANGE_STATE):      row(ORG,  DEPT, NO,   NO,    NO,    NO,   NO),
    # ---------------------------------------------------------------- membership
    (R.ORGANIZATION_MEMBERSHIP, A.CREATE):       row(ORG, NO,  NO,  NO,   NO,  NO,  NO),
    (R.ORGANIZATION_MEMBERSHIP, A.READ):         row(ORG, ORG, ORG, ORG,  ORG, ORG, ORG),
    (R.ORGANIZATION_MEMBERSHIP, A.LIST):         row(ORG, ORG, ORG, ORG,  ORG, ORG, ORG),
    (R.ORGANIZATION_MEMBERSHIP, A.UPDATE):       row(ORG, NO,  NO,  NO,   NO,  NO,  NO),
    (R.ORGANIZATION_MEMBERSHIP, A.CHANGE_STATE): row(ORG, NO,  NO,  NO,   NO,  NO,  NO),
    # ---------------------------------------------------------------- role assignment
    (R.ROLE_ASSIGNMENT, A.CREATE):       row(ORG, NO,   NO,   NO,   NO, NO,  NO),
    (R.ROLE_ASSIGNMENT, A.READ):         row(ORG, DEPT, TEAM, SELF, NO, ORG, NO),
    (R.ROLE_ASSIGNMENT, A.LIST):         row(ORG, DEPT, TEAM, SELF, NO, ORG, NO),
    (R.ROLE_ASSIGNMENT, A.CHANGE_STATE): row(ORG, NO,   NO,   NO,   NO, NO,  NO),
    # ---------------------------------------------------------------- external identity
    # ADR-0037. Reading is organization-wide because attribution has to be explicable to the people
    # it affects. Everything that writes is `org_admin`, and no action carries SELF: a confirmed
    # mapping is an authority to speak as somebody, so claiming a handle and being believed about it
    # must not be the same act.
    (R.EXTERNAL_IDENTITY, A.CREATE):       row(ORG, NO,  NO,  NO,  NO,  NO,  NO),
    (R.EXTERNAL_IDENTITY, A.READ):         row(ORG, ORG, ORG, ORG, ORG, ORG, ORG),
    (R.EXTERNAL_IDENTITY, A.LIST):         row(ORG, ORG, ORG, ORG, ORG, ORG, ORG),
    (R.EXTERNAL_IDENTITY, A.UPDATE):       row(ORG, NO,  NO,  NO,  NO,  NO,  NO),
    (R.EXTERNAL_IDENTITY, A.CHANGE_STATE): row(ORG, NO,  NO,  NO,  NO,  NO,  NO),
    # ---------------------------------------------------------------- project
    (R.PROJECT, A.CREATE):            row(ORG, DEPT, TEAM, NO,          NO,  NO,  NO),
    (R.PROJECT, A.READ):              row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.PROJECT, A.LIST):              row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.PROJECT, A.UPDATE):            row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    (R.PROJECT, A.CHANGE_STATE):      row(ORG, DEPT, TEAM, NO,          NO,  NO,  NO),
    (R.PROJECT, A.CHANGE_VISIBILITY): row(ORG, DEPT, TEAM, NO,          NO,  NO,  NO),
    # ---------------------------------------------------------------- milestone
    (R.MILESTONE, A.CREATE):       row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    (R.MILESTONE, A.READ):         row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.MILESTONE, A.LIST):         row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.MILESTONE, A.UPDATE):       row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    (R.MILESTONE, A.CHANGE_STATE): row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    # ---------------------------------------------------------------- work
    (R.WORK, A.CREATE):            row(ORG, DEPT, TEAM, ORG,         NO,  NO,  NO),
    (R.WORK, A.READ):              row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.WORK, A.LIST):              row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.WORK, A.UPDATE):            row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    (R.WORK, A.CHANGE_STATE):      row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    (R.WORK, A.CHANGE_VISIBILITY): row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    # ---------------------------------------------------------------- work assignment
    (R.WORK_ASSIGNMENT, A.READ):           row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.WORK_ASSIGNMENT, A.LIST):           row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.WORK_ASSIGNMENT, A.ASSIGN):         row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    (R.WORK_ASSIGNMENT, A.REASSIGN):       row(ORG, DEPT, TEAM, NO,          NO,  NO,  NO),
    (R.WORK_ASSIGNMENT, A.END_ASSIGNMENT): row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    # ---------------------------------------------------------------- dependency
    (R.DEPENDENCY, A.CREATE):       row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    (R.DEPENDENCY, A.READ):         row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.DEPENDENCY, A.LIST):         row(ORG, DEPT, TEAM, TEAM_OR_OWN, ORG, ORG, ORG),
    (R.DEPENDENCY, A.CHANGE_STATE): row(ORG, DEPT, TEAM, OWN,         NO,  NO,  NO),
    # ---------------------------------------------------------------- audit
    (R.AUDIT_ENTRY, A.READ): row(ORG, NO, NO, NO, NO, ORG, NO),
    (R.AUDIT_ENTRY, A.LIST): row(ORG, NO, NO, NO, NO, ORG, NO),
}


def declared_pairs() -> set[tuple[ResourceType, Action]]:
    return set(MATRIX)


def expected_pairs() -> set[tuple[ResourceType, Action]]:
    return {
        (resource, action)
        for resource, actions in RESOURCE_ACTIONS.items()
        for action in actions
    }
