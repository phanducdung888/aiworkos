"""Published interface of the Identity & Organization context.

Other contexts import from here and from nowhere else inside this package (ADR-0001). The contract
is intentionally narrow: Phase 1 exposes the persistence models that the platform needs in order to
resolve a principal, and the read-side structural questions other contexts must ask in order to
authorize their own resources. Write-side application services arrive in a later checkpoint and will
be exported from this module, not from `models`.
"""

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

__all__ = [
    "Department",
    "ExternalIdentity",
    "Organization",
    "OrganizationMembership",
    "Person",
    "RoleAssignment",
    "Team",
    "TeamMembership",
    "departments_led_by",
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
