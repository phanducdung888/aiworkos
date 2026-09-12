"""Identity persistence.

The only module in this context that talks to PostgreSQL for writes. Same shape as the Work Core's
repository and for the same reasons: reads are scoped twice (predicate and RLS, BR-G-01a), and every
update carries a version guard so optimistic concurrency is a property of the statement rather than
of a read-then-write sequence (BR-G-06).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, cast

from sqlalchemy import CursorResult, Update, func, select, update
from sqlalchemy.orm import Session

from app.contexts.identity.domain import (
    ExistingExternalIdentity,
    ExistingRoleAssignment,
    ExistingTeamMembership,
    ScopeType,
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
from app.platform.concurrency import StaleVersionError
from app.platform.ids import uuid7


def _apply(
    session: Session, stmt: Update, *, resource: str, entity_id: uuid.UUID, expected: int
) -> None:
    result = cast("CursorResult[Any]", session.execute(stmt))
    if result.rowcount == 0:
        raise StaleVersionError(resource, entity_id, expected)


# --------------------------------------------------------------------------- organization


def get_organization(session: Session, *, org_id: uuid.UUID) -> Organization | None:
    return session.execute(
        select(Organization)
        .where(Organization.id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def update_organization(
    session: Session, *, org_id: uuid.UUID, expected_version: int, **values: Any
) -> Organization:
    _apply(
        session,
        update(Organization)
        .where(Organization.id == org_id, Organization.version == expected_version)
        .values(**values, version=Organization.version + 1, updated_at=func.now()),
        resource="organization",
        entity_id=org_id,
        expected=expected_version,
    )
    organization = get_organization(session, org_id=org_id)
    assert organization is not None  # noqa: S101
    return organization


# --------------------------------------------------------------------------- person


def get_person(session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID) -> Person | None:
    return session.execute(
        select(Person)
        .where(Person.id == person_id, Person.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def person_by_subject(session: Session, *, org_id: uuid.UUID, subject: str) -> Person | None:
    """ADR-0036. One subject maps to one Person per organization, never globally."""
    return session.execute(
        select(Person).where(Person.org_id == org_id, Person.keycloak_subject == subject)
    ).scalar_one_or_none()


def insert_person(session: Session, *, org_id: uuid.UUID, **values: Any) -> Person:
    person = Person(id=uuid7(), org_id=org_id, **values)
    session.add(person)
    session.flush()
    return person


def update_person(
    session: Session,
    *,
    org_id: uuid.UUID,
    person_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> Person:
    _apply(
        session,
        update(Person)
        .where(
            Person.id == person_id,
            Person.org_id == org_id,
            Person.version == expected_version,
        )
        .values(**values, version=Person.version + 1, updated_at=func.now()),
        resource="person",
        entity_id=person_id,
        expected=expected_version,
    )
    person = get_person(session, org_id=org_id, person_id=person_id)
    assert person is not None  # noqa: S101
    return person


# --------------------------------------------------------------------------- department


def get_department(
    session: Session, *, org_id: uuid.UUID, department_id: uuid.UUID
) -> Department | None:
    return session.execute(
        select(Department)
        .where(Department.id == department_id, Department.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def department_ancestors(
    session: Session, *, org_id: uuid.UUID, department_id: uuid.UUID
) -> list[uuid.UUID]:
    """The chain above a department, nearest first, for BR-I-01.

    Walked in Python rather than as a recursive CTE because the depth limit is five: a loop that
    runs at most five times is cheaper to read than a CTE, and the guard below stops a hierarchy
    already corrupted by a repair script from spinning here while the validation that would reject
    it waits for a result.
    """
    chain: list[uuid.UUID] = []
    cursor: uuid.UUID | None = department_id
    for _ in range(10):
        if cursor is None:
            break
        parent = session.execute(
            select(Department.parent_department_id).where(
                Department.id == cursor, Department.org_id == org_id
            )
        ).scalar_one_or_none()
        if parent is None:
            break
        chain.append(parent)
        cursor = parent
    return chain


def insert_department(session: Session, *, org_id: uuid.UUID, **values: Any) -> Department:
    department = Department(id=uuid7(), org_id=org_id, **values)
    session.add(department)
    session.flush()
    return department


def update_department(
    session: Session,
    *,
    org_id: uuid.UUID,
    department_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> Department:
    _apply(
        session,
        update(Department)
        .where(
            Department.id == department_id,
            Department.org_id == org_id,
            Department.version == expected_version,
        )
        .values(**values, version=Department.version + 1, updated_at=func.now()),
        resource="department",
        entity_id=department_id,
        expected=expected_version,
    )
    department = get_department(session, org_id=org_id, department_id=department_id)
    assert department is not None  # noqa: S101
    return department


# --------------------------------------------------------------------------- team


def get_team(session: Session, *, org_id: uuid.UUID, team_id: uuid.UUID) -> Team | None:
    return session.execute(
        select(Team)
        .where(Team.id == team_id, Team.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_team(session: Session, *, org_id: uuid.UUID, **values: Any) -> Team:
    team = Team(id=uuid7(), org_id=org_id, **values)
    session.add(team)
    session.flush()
    return team


def update_team(
    session: Session,
    *,
    org_id: uuid.UUID,
    team_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> Team:
    _apply(
        session,
        update(Team)
        .where(Team.id == team_id, Team.org_id == org_id, Team.version == expected_version)
        .values(**values, version=Team.version + 1, updated_at=func.now()),
        resource="team",
        entity_id=team_id,
        expected=expected_version,
    )
    team = get_team(session, org_id=org_id, team_id=team_id)
    assert team is not None  # noqa: S101
    return team


# --------------------------------------------------------------------------- team membership


def team_memberships(
    session: Session, *, org_id: uuid.UUID, team_id: uuid.UUID
) -> list[TeamMembership]:
    return list(
        session.execute(
            select(TeamMembership)
            .where(TeamMembership.org_id == org_id, TeamMembership.team_id == team_id)
            .order_by(TeamMembership.valid_from, TeamMembership.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def existing_team_memberships(
    session: Session, *, org_id: uuid.UUID, team_id: uuid.UUID
) -> list[ExistingTeamMembership]:
    return [
        ExistingTeamMembership(
            person_id=row.person_id, valid_from=row.valid_from, valid_to=row.valid_to
        )
        for row in team_memberships(session, org_id=org_id, team_id=team_id)
    ]


def get_team_membership(
    session: Session, *, org_id: uuid.UUID, membership_id: uuid.UUID
) -> TeamMembership | None:
    return session.execute(
        select(TeamMembership)
        .where(TeamMembership.id == membership_id, TeamMembership.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_team_membership(
    session: Session, *, org_id: uuid.UUID, **values: Any
) -> TeamMembership:
    membership = TeamMembership(id=uuid7(), org_id=org_id, **values)
    session.add(membership)
    session.flush()
    return membership


def end_team_membership(
    session: Session,
    *,
    org_id: uuid.UUID,
    membership_id: uuid.UUID,
    expected_version: int,
    valid_to: dt.datetime,
) -> TeamMembership:
    """BR-I-03. Ending sets `valid_to`; the row is never removed."""
    _apply(
        session,
        update(TeamMembership)
        .where(
            TeamMembership.id == membership_id,
            TeamMembership.org_id == org_id,
            TeamMembership.version == expected_version,
        )
        .values(valid_to=valid_to, version=TeamMembership.version + 1, updated_at=func.now()),
        resource="team_membership",
        entity_id=membership_id,
        expected=expected_version,
    )
    membership = get_team_membership(session, org_id=org_id, membership_id=membership_id)
    assert membership is not None  # noqa: S101
    return membership


# --------------------------------------------------------------------------- org membership


def get_organization_membership(
    session: Session, *, org_id: uuid.UUID, membership_id: uuid.UUID
) -> OrganizationMembership | None:
    return session.execute(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.id == membership_id,
            OrganizationMembership.org_id == org_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def membership_for_person(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> OrganizationMembership | None:
    return session.execute(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.org_id == org_id,
            OrganizationMembership.person_id == person_id,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_organization_membership(
    session: Session, *, org_id: uuid.UUID, **values: Any
) -> OrganizationMembership:
    membership = OrganizationMembership(id=uuid7(), org_id=org_id, **values)
    session.add(membership)
    session.flush()
    return membership


def update_organization_membership(
    session: Session,
    *,
    org_id: uuid.UUID,
    membership_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> OrganizationMembership:
    _apply(
        session,
        update(OrganizationMembership)
        .where(
            OrganizationMembership.id == membership_id,
            OrganizationMembership.org_id == org_id,
            OrganizationMembership.version == expected_version,
        )
        .values(
            **values,
            version=OrganizationMembership.version + 1,
            updated_at=func.now(),
        ),
        resource="organization_membership",
        entity_id=membership_id,
        expected=expected_version,
    )
    membership = get_organization_membership(
        session, org_id=org_id, membership_id=membership_id
    )
    assert membership is not None  # noqa: S101
    return membership


# --------------------------------------------------------------------------- role assignment


def role_assignments_for(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> list[RoleAssignment]:
    return list(
        session.execute(
            select(RoleAssignment)
            .where(RoleAssignment.org_id == org_id, RoleAssignment.person_id == person_id)
            .order_by(RoleAssignment.granted_at, RoleAssignment.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def existing_role_assignments(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> list[ExistingRoleAssignment]:
    return [
        ExistingRoleAssignment(
            role=row.role,
            scope_type=ScopeType(row.scope_type),
            scope_id=row.scope_id,
            revoked_at=row.revoked_at,
        )
        for row in role_assignments_for(session, org_id=org_id, person_id=person_id)
    ]


def get_role_assignment(
    session: Session, *, org_id: uuid.UUID, assignment_id: uuid.UUID
) -> RoleAssignment | None:
    return session.execute(
        select(RoleAssignment)
        .where(RoleAssignment.id == assignment_id, RoleAssignment.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_role_assignment(
    session: Session, *, org_id: uuid.UUID, **values: Any
) -> RoleAssignment:
    grant = RoleAssignment(id=uuid7(), org_id=org_id, **values)
    session.add(grant)
    session.flush()
    return grant


def revoke_role_assignment(
    session: Session,
    *,
    org_id: uuid.UUID,
    assignment_id: uuid.UUID,
    expected_version: int,
    revoked_at: dt.datetime,
) -> RoleAssignment:
    _apply(
        session,
        update(RoleAssignment)
        .where(
            RoleAssignment.id == assignment_id,
            RoleAssignment.org_id == org_id,
            RoleAssignment.version == expected_version,
        )
        .values(
            revoked_at=revoked_at, version=RoleAssignment.version + 1, updated_at=func.now()
        ),
        resource="role_assignment",
        entity_id=assignment_id,
        expected=expected_version,
    )
    grant = get_role_assignment(session, org_id=org_id, assignment_id=assignment_id)
    assert grant is not None  # noqa: S101
    return grant


# --------------------------------------------------------------------------- external identity


def external_identities(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID | None = None
) -> list[ExternalIdentity]:
    statement = select(ExternalIdentity).where(ExternalIdentity.org_id == org_id)
    if person_id is not None:
        statement = statement.where(ExternalIdentity.person_id == person_id)
    return list(
        session.execute(
            statement.order_by(ExternalIdentity.created_at, ExternalIdentity.id)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def existing_external_identities(
    session: Session, *, org_id: uuid.UUID
) -> list[ExistingExternalIdentity]:
    """BR-I-07 is organization-wide, so the uniqueness check is too."""
    return [
        ExistingExternalIdentity(
            source_system=row.source_system,
            external_id=row.external_id,
            person_id=row.person_id,
        )
        for row in external_identities(session, org_id=org_id)
    ]


def get_external_identity(
    session: Session, *, org_id: uuid.UUID, identity_id: uuid.UUID
) -> ExternalIdentity | None:
    return session.execute(
        select(ExternalIdentity)
        .where(ExternalIdentity.id == identity_id, ExternalIdentity.org_id == org_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def insert_external_identity(
    session: Session, *, org_id: uuid.UUID, **values: Any
) -> ExternalIdentity:
    mapping = ExternalIdentity(id=uuid7(), org_id=org_id, **values)
    session.add(mapping)
    session.flush()
    return mapping


def update_external_identity(
    session: Session,
    *,
    org_id: uuid.UUID,
    identity_id: uuid.UUID,
    expected_version: int,
    **values: Any,
) -> ExternalIdentity:
    _apply(
        session,
        update(ExternalIdentity)
        .where(
            ExternalIdentity.id == identity_id,
            ExternalIdentity.org_id == org_id,
            ExternalIdentity.version == expected_version,
        )
        .values(**values, version=ExternalIdentity.version + 1, updated_at=func.now()),
        resource="external_identity",
        entity_id=identity_id,
        expected=expected_version,
    )
    mapping = get_external_identity(session, org_id=org_id, identity_id=identity_id)
    assert mapping is not None  # noqa: S101
    return mapping


# --------------------------------------------------------------------------- references (ADR-0035)


def person_exists(session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    return get_person(session, org_id=org_id, person_id=person_id) is not None


def team_exists(session: Session, *, org_id: uuid.UUID, team_id: uuid.UUID) -> bool:
    return get_team(session, org_id=org_id, team_id=team_id) is not None


def department_exists(
    session: Session, *, org_id: uuid.UUID, department_id: uuid.UUID
) -> bool:
    return get_department(session, org_id=org_id, department_id=department_id) is not None
