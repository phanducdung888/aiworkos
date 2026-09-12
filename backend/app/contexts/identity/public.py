"""Published interface of the Identity & Organization context.

Other contexts import from here and from nowhere else inside this package (ADR-0001). The contract
is intentionally narrow, and it is the *only* way in: persistence models for the platform's schema
tooling, the read-side structural questions other contexts ask to authorize their own resources,
the application services, and the reference predicates of ADR-0035.

Identity depends on nothing. Work Core depends on Identity and reaches it through this module —
never through `models`, `repository` or `queries`. That direction is enforced three ways: the
import-linter `context-isolation` contract permits exactly one edge,
`test_identity_never_imports_the_work_core` walks the AST, and
`test_the_cross_context_exception_list_stays_one_directional` guards the configuration itself.
"""

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
    TeamRole,
    UnitStatus,
    may_attribute,
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
from app.contexts.identity.queries import (
    departments_led_by,
    get_department,
    get_person,
    get_team,
    list_departments,
    list_people,
    list_teams,
    person_status,
    team_ids_for_person,
    team_ids_in_departments,
)
from app.contexts.identity.references import (
    assert_department_exists,
    assert_person_exists,
    assert_team_exists,
)
from app.contexts.identity.repository import (
    external_identities as external_identity_rows,
)
from app.contexts.identity.repository import (
    role_assignments_for as role_rows,
)
from app.contexts.identity.repository import (
    team_memberships as team_member_rows,
)
from app.contexts.identity.services import (
    DepartmentService,
    ExternalIdentityService,
    MembershipService,
    OrganizationService,
    PersonService,
    RoleService,
    ServiceContext,
    TeamService,
)

__all__ = [
    "Department",
    "ExternalIdentity",
    "Organization",
    "OrganizationMembership",
    "Person",
    "RoleAssignment",
    "Team",
    "TeamMembership",
    "AddTeamMember",
    "ChangeMembershipStatus",
    "ChangePersonStatus",
    "ChangeUnitStatus",
    "ConfirmExternalIdentity",
    "CreateDepartment",
    "CreateExternalIdentity",
    "CreateMembership",
    "CreatePerson",
    "CreateTeam",
    "EndTeamMembership",
    "GrantRole",
    "RevokeRole",
    "UpdateDepartment",
    "UpdateExternalIdentity",
    "UpdateOrganization",
    "UpdatePerson",
    "UpdateTeam",
    "DepartmentService",
    "MembershipStatus",
    "PersonStatus",
    "ScopeType",
    "TeamRole",
    "UnitStatus",
    "may_attribute",
    "ExternalIdentityService",
    "MembershipService",
    "OrganizationService",
    "PersonService",
    "RoleService",
    "ServiceContext",
    "TeamService",
    "assert_department_exists",
    "assert_person_exists",
    "assert_team_exists",
    "departments_led_by",
    "external_identity_rows",
    "role_rows",
    "team_member_rows",
    "get_department",
    "get_person",
    "get_team",
    "list_departments",
    "list_people",
    "list_teams",
    "person_status",
    "team_ids_for_person",
    "team_ids_in_departments",
]
