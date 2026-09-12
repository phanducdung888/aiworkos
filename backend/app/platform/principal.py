"""Turning an authenticated subject into an authorization principal.

    Keycloak subject + X-Organization-Id -> Person -> OrganizationMembership -> RoleAssignment

Resolved once per request and from local rows only. The token never carries the organization: one
shared realm serves every tenant (ADR-0031), a human may work for several organizations, and a claim
that said which one would be a claim that keeps saying it after the membership is revoked
(security-model §2).

The organization arrives in an explicit header and is never inferred — not from a single membership,
not from a default. Inference of this kind works perfectly until the first person joins a second
organization, and then silently acts on the wrong one.

Two failure modes, deliberately different. A request with no header is a malformed request and says
so. A request naming an organization the caller is not in is indistinguishable from one naming an
organization that does not exist, because whether a given organization exists here is itself tenant
information (contract §5).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.authz import Principal, Role


class OrganizationContextRequired(Exception):
    """No `X-Organization-Id` header, or not a UUID."""


class OrganizationNotResolvable(Exception):
    """The caller has no active membership of that organization — or it does not exist.

    One exception for both, and the handler renders it as 404. Distinguishing them would answer
    "does organization X exist?" for anybody with a valid token and a list of guesses.
    """


class NoRolesHeld(Exception):
    """A known member of the organization holding no active role assignment.

    Not an error in the data: somebody can be a member before anybody grants them anything. It is an
    authorization outcome, and it renders as 403 — the caller is known and is permitted nothing.
    """


_PERSON = text(
    """
    SELECT p.id
    FROM person p
    JOIN organization_membership m ON m.person_id = p.id AND m.org_id = p.org_id
    WHERE p.org_id = :org_id
      AND p.keycloak_subject = :subject
      AND p.status = 'active'
      AND m.status = 'active'
    """
)

_ROLES = text(
    """
    SELECT DISTINCT role FROM role_assignment
    WHERE org_id = :org_id AND person_id = :person_id AND revoked_at IS NULL
    """
)


def parse_organization_header(raw: str | None) -> uuid.UUID:
    if raw is None or not raw.strip():
        raise OrganizationContextRequired("X-Organization-Id is required on every request")
    try:
        return uuid.UUID(raw.strip())
    except ValueError as exc:
        raise OrganizationContextRequired("X-Organization-Id must be a UUID") from exc


def resolve_principal(
    session: Session, *, subject: str, org_id: uuid.UUID
) -> Principal:
    """Resolve the principal, or explain which step failed.

    The session must already be bound to `org_id`, so RLS is doing the same scoping the predicates
    below do. Both, not either: application scoping and RLS are independent controls and neither is
    allowed to become the reason for dropping the other (BR-G-01a, contract §4).
    """
    person_id = session.execute(
        _PERSON, {"org_id": org_id, "subject": subject}
    ).scalar_one_or_none()
    if person_id is None:
        raise OrganizationNotResolvable(
            "no active membership of the requested organization"
        )

    # A role the application does not know is skipped rather than fatal. The column has a CHECK
    # constraint, so this only fires if a migration adds a role before the code learns it, and in
    # that window an unknown role should grant nothing — not crash every request the person makes.
    known = {role.value for role in Role}
    roles = {
        Role(value)
        for value in session.execute(
            _ROLES, {"org_id": org_id, "person_id": person_id}
        ).scalars()
        if value in known
    }
    if not roles:
        raise NoRolesHeld("this person holds no active role in the organization")

    return Principal(person_id=person_id, org_id=org_id, roles=frozenset(roles))


def resolve_principal_for(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> Principal:
    """A principal for a person the system already trusts, without a bearer token.

    Used by the worker (ADR-0044): a queued job has no request and no token, but the authority it
    executes under must still be a real person's real roles, read from the database at execution
    time rather than captured when the job was queued. Roles can be revoked between approval and
    execution, and the later read is the one that should win.

    Not an authentication path. It takes a `person_id` the caller already established — from an
    ApprovalRecord written under an authenticated request — and never a subject claimed by anybody.
    """
    roles = {
        Role(value)
        for value in session.execute(
            _ROLES, {"org_id": org_id, "person_id": person_id}
        ).scalars()
        if value in {role.value for role in Role}
    }
    if not roles:
        raise NoRolesHeld("this person holds no active role in the organization")
    return Principal(person_id=person_id, org_id=org_id, roles=frozenset(roles))
