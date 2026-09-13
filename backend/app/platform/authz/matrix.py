"""The authorization matrix.

This file is data, not logic. It is the specification of who may do what, and it is reviewed as a
spec. `tests/unit/test_authz_matrix.py` asserts that every applicable (resource, action) pair has a
declaration for every role, so adding a role, resource or action breaks the build until the new
cells are filled in deliberately.

Column order in every row is fixed:

    admin, dept_lead, team_lead, member, viewer, auditor, executive, ingestion

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
    Role.INGESTION,
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
    ingestion: Cell = NO,
) -> dict[Role, frozenset[Grant]]:
    """One row of the table. Every role's cell, and `ingestion` defaults to denial.

    The default is the one departure from this file's usual discipline — adding a role normally
    breaks the build until every cell is filled in deliberately, which is what stops a new role
    being granted something by accident. `ingestion` is defined as *denied everywhere but one
    cell*, so a default of `NO` says exactly that, and spelling it out sixty times would bury the
    one place it is not. `test_the_ingestion_role_holds_exactly_one_grant` replaces the build break
    and is the stronger assertion: it checks the whole matrix rather than checking that somebody
    typed something.
    """
    values = (admin, dept_lead, team_lead, member, viewer, auditor, executive, ingestion)
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
    # No UPDATE. An Event is immutable (BR-E-01, ADR-0038) and the fields that do change —
    # processing status, participant resolution, retention — belong to pipelines that do not yet
    # exist. Declaring a permission nobody can exercise would be a guess about how they will work.
    R.EVENT: frozenset({A.CREATE, A.READ, A.LIST, A.ATTACH}),
    R.EVIDENCE: frozenset({A.CREATE, A.READ, A.LIST, A.SUPERSEDE}),
    # No DELETE and no plain UPDATE. A Commitment moves through BR-C-04's transitions and nothing
    # else; `withdrawn`, `cancelled` and `disputed` are states, which is what BR-G-04 means by
    # removal being a lifecycle rather than an operation.
    R.COMMITMENT: frozenset({A.CREATE, A.READ, A.LIST, A.UPDATE, A.CHANGE_STATE}),
    # APPROVE covers rejection too: both are the same act of deciding, and splitting them would
    # invite a role that may reject but not approve, which is a veto rather than a review.
    R.PROPOSAL: frozenset({A.CREATE, A.READ, A.LIST, A.UPDATE, A.APPROVE}),
    # No CREATE: a policy cell is set or cleared, never created and deleted, so
    # there is exactly one row per cell and one answer to 'what is the policy'.
    R.AGENT_CAPABILITY_POLICY: frozenset({A.READ, A.LIST, A.UPDATE}),
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
    # ---------------------------------------------------------------- agent capability policy
    # Deciding how much autonomy an organization grants is an administrative act, not a team one.
    # Reading it is wider: somebody asked to review an AI-raised Proposal is entitled to know what
    # the organization decided the AI may do.
    #                                          admin dept team member viewer audit exec
    (R.AGENT_CAPABILITY_POLICY, A.READ):   row(ORG,  ORG, ORG, ORG,   ORG,   ORG,  ORG),
    (R.AGENT_CAPABILITY_POLICY, A.LIST):   row(ORG,  ORG, ORG, ORG,   ORG,   ORG,  ORG),
    (R.AGENT_CAPABILITY_POLICY, A.UPDATE): row(ORG,  NO,  NO,  NO,    NO,    NO,   NO),
    # ---------------------------------------------------------------- audit
    # ---------------------------------------------------------------- event (capture)
    # Capture is the act the product exists for, so every working role can do it and the read-only
    # roles cannot. READ and LIST are organization-wide here and narrowed afterwards by sensitivity
    # (BR-E-08): a `restricted` Event reaches only its participants and an auditor, and that
    # narrowing is a query predicate rather than a grant, matching how Work visibility already
    # works. Putting it in the matrix instead would need a Relation the policy engine cannot
    # evaluate without reading the participant table, which it is not allowed to do.
    #                                 admin  dept  team  member viewer audit exec
    # The one cell a connector holds (ADR-0060). `ORG`, because a delivered message belongs to
    # the organization the credential is scoped to and to no narrower thing inside it.
    (R.EVENT, A.CREATE):             row(ORG,  ORG,  ORG,  ORG,   NO,    NO,   NO,  ORG),
    # `OWN` for ingestion: the Events it captured, and nothing else (ADR-0063). The narrowing
    # is real — `signal.queries._reach_predicate` renders `PERSONAL` as
    # `captured_by_person_id = :me`, and the sensitivity predicate still applies on top.
    (R.EVENT, A.READ):               row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG,  OWN),
    (R.EVENT, A.LIST):               row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    # Attaching is narrower than capturing. Adding a file to an Event somebody else recorded is a
    # change to their record of what happened, so a member reaches only their own captures
    # (ADR-0039 routes every attachment endpoint through the Event's own authorization).
    # Attaching to an Event it delivered. `event_relations` already answers `PERSONAL` for
    # whoever captured the Event, so this cell is the whole change: a connector attaching to
    # somebody else's Event is refused by machinery that predates it.
    (R.EVENT, A.ATTACH):             row(ORG,  DEPT, TEAM, OWN,   NO,    NO,   NO,  OWN),
    # ---------------------------------------------------------------- evidence
    # Evidence is the citation layer: it says *why* the system believes something. Reading it has to
    # be as wide as reading the thing it justifies, or a claim becomes unexplainable to the person
    # it affects. Creating it is narrower, because asserting that an Event supports a claim is an
    # assertion in its own right.
    #                                 admin  dept  team  member viewer audit exec
    (R.EVIDENCE, A.CREATE):          row(ORG,  ORG,  ORG,  ORG,   NO,    NO,   NO),
    (R.EVIDENCE, A.READ):            row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.EVIDENCE, A.LIST):            row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    # Superseding is a correction of the record, so it is not a member's to make on somebody else's
    # citation — `PERSONAL` reaches the Evidence they produced themselves.
    (R.EVIDENCE, A.SUPERSEDE):       row(ORG,  DEPT, TEAM, OWN,   NO,    NO,   NO),
    # ---------------------------------------------------------------- commitment
    # BR-C-09 names who may change a Commitment's status: the committer, the recipient, or a lead in
    # their management chain. `PERSONAL` carries the first two and the department and team grants
    # carry the third; the service supplies which of them holds.
    (R.COMMITMENT, A.CREATE):        row(ORG,  ORG,  ORG,  ORG,   NO,    NO,   NO),
    (R.COMMITMENT, A.READ):          row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.COMMITMENT, A.LIST):          row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.COMMITMENT, A.UPDATE):        row(ORG,  DEPT, TEAM, OWN,   NO,    NO,   NO),
    (R.COMMITMENT, A.CHANGE_STATE):  row(ORG,  DEPT, TEAM, OWN,   NO,    NO,   NO),
    # ---------------------------------------------------------------- proposal
    # BR-PR-04 routes a Proposal to the person with authority over its target, so the person who
    # can approve is the person who could have made the change themselves. That is also why
    # approving grants nothing (BR-PR-05): the Tool Gateway executes as the approver and the
    # service authorizes them normally, so a grant here is permission to *decide*, not to act.
    (R.PROPOSAL, A.CREATE):          row(ORG,  ORG,  ORG,  ORG,   NO,    NO,   NO),
    (R.PROPOSAL, A.READ):            row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    (R.PROPOSAL, A.LIST):            row(ORG,  ORG,  ORG,  ORG,   ORG,   ORG,  ORG),
    # Revising is the proposer tidying their own proposal before anybody decides it.
    (R.PROPOSAL, A.UPDATE):          row(ORG,  DEPT, TEAM, OWN,   NO,    NO,   NO),
    (R.PROPOSAL, A.APPROVE):         row(ORG,  DEPT, TEAM, OWN,   NO,    NO,   NO),
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
