"""Read-side questions about people and organizational structure.

Authorization *decisions* are made in `platform/authz`. The *facts* those decisions rest on — is
this actor in that team, does that department sit under one they lead, is this person still active
— belong to Identity, because only Identity knows what they mean. Publishing them here is what lets
the Work Core authorize its own resources without reaching into identity's tables (ADR-0001).

Every query filters on `org_id` explicitly even though RLS already does. The redundancy is
deliberate: application scoping and RLS are two independent controls and neither is allowed to
become the reason the other can be dropped (BR-G-01a).
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from app.contexts.identity.domain import may_attribute
from app.contexts.identity.models import (
    Department,
    ExternalIdentity,
    Person,
    Team,
)
from app.platform.authz import Action, Grant, Principal, ResourceType, grants_for
from app.platform.http.pagination import Cursor, encode_cursor

_PERSON_STATUS = text(
    "SELECT status FROM person WHERE id = :person_id AND org_id = :org_id"
)

# Membership is time-bounded and ending it sets `valid_to` rather than deleting the row (BR-I-03),
# so "is a member" is a question about now, not about the row existing. A team lead is included
# whether or not anybody remembered to also give them a membership row.
_TEAMS_FOR_PERSON = text(
    """
    SELECT team_id FROM team_membership
    WHERE person_id = :person_id
      AND org_id = :org_id
      AND valid_from <= now()
      AND (valid_to IS NULL OR valid_to > now())
    UNION
    SELECT id FROM team
    WHERE lead_person_id = :person_id AND org_id = :org_id AND status = 'active'
    """
)

# A department lead's reach is the subtree, not the single node (BR-I-01 guarantees it is a tree,
# so this terminates).
_DEPARTMENTS_LED_BY = text(
    """
    WITH RECURSIVE led AS (
        SELECT id FROM department
        WHERE lead_person_id = :person_id AND org_id = :org_id AND status = 'active'
        UNION
        SELECT d.id FROM department d
        JOIN led ON d.parent_department_id = led.id
        WHERE d.org_id = :org_id
    )
    SELECT id FROM led
    """
)

_TEAMS_IN_DEPARTMENTS = text(
    "SELECT id FROM team WHERE org_id = :org_id AND department_id = ANY(:department_ids)"
)


def person_status(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> str | None:
    """The person's lifecycle status, or None if they are not in this organization.

    None and `departed` are different answers and callers must treat them differently: the first is
    a reference to somebody who does not exist here, the second is somebody who does and may not be
    given new work (BR-I-05).
    """
    row = session.execute(
        _PERSON_STATUS, {"person_id": person_id, "org_id": org_id}
    ).scalar()
    return str(row) if row is not None else None


def team_ids_for_person(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """Teams the person is currently in or leads."""
    rows = session.execute(
        _TEAMS_FOR_PERSON, {"person_id": person_id, "org_id": org_id}
    ).scalars()
    return frozenset(rows)


def departments_led_by(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """Every department in the subtree of any department this person leads."""
    rows = session.execute(
        _DEPARTMENTS_LED_BY, {"person_id": person_id, "org_id": org_id}
    ).scalars()
    return frozenset(rows)


def team_ids_in_departments(
    session: Session, *, org_id: uuid.UUID, department_ids: frozenset[uuid.UUID]
) -> frozenset[uuid.UUID]:
    """Teams belonging to any of the given departments. Empty in, empty out."""
    if not department_ids:
        return frozenset()
    rows = session.execute(
        _TEAMS_IN_DEPARTMENTS,
        {"org_id": org_id, "department_ids": list(department_ids)},
    ).scalars()
    return frozenset(rows)


# --------------------------------------------------------------------------- directory reads


def _readable(
    model: type[Any], principal: Principal, action: Action, resource: ResourceType
) -> Any:
    """Identity rows carry no visibility column, so reach from the matrix is the whole answer.

    Unlike Work and Project (BR-W-18, BR-P-09) there is no second dimension here: a Person is either
    within the caller's reach or not. Every role in the matrix holds an ORG grant on `PERSON.READ`,
    `TEAM.READ` and `DEPARTMENT.READ` — a directory everybody in an organization can see is the
    point of a directory — so the predicate below is normally just the tenant boundary. It is still
    written out, because a role added later with a narrower grant must narrow this too rather than
    silently inherit the whole organization.
    """
    grants = grants_for(principal, action, resource)
    if Grant.ORG in grants:
        return model.org_id == principal.org_id
    if not grants:
        return model.id.is_(None)
    # No non-ORG grant is declared for these reads today. Refusing rather than guessing keeps the
    # matrix the only place that decides.
    return model.id.is_(None)


def list_people(
    session: Session,
    principal: Principal,
    *,
    query: str | None = None,
    include_departed: bool = False,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> tuple[list[Person], str | None]:
    """The people picker behind every assignment field.

    Departed people are excluded by default because BR-I-05 stops them receiving new assignments, so
    offering them in a picker offers a choice the service will refuse. They remain requestable,
    because "who owned this in March" is a question about somebody who has since left.
    """
    statement = select(Person).where(
        _readable(Person, principal, Action.LIST, ResourceType.PERSON)
    )
    if not include_departed:
        statement = statement.where(Person.status == "active")
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            or_(Person.display_name.ilike(pattern), Person.email.ilike(pattern))
        )
    return _page(session, statement, Person, limit, cursor)


def get_person(
    session: Session, principal: Principal, person_id: uuid.UUID
) -> Person | None:
    return session.execute(
        select(Person)
        .where(_readable(Person, principal, Action.READ, ResourceType.PERSON))
        .where(Person.id == person_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_teams(
    session: Session,
    principal: Principal,
    *,
    department_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> tuple[list[Team], str | None]:
    statement = select(Team).where(
        _readable(Team, principal, Action.LIST, ResourceType.TEAM),
        Team.status == "active",
    )
    if department_id is not None:
        statement = statement.where(Team.department_id == department_id)
    return _page(session, statement, Team, limit, cursor)


def get_team(session: Session, principal: Principal, team_id: uuid.UUID) -> Team | None:
    return session.execute(
        select(Team)
        .where(_readable(Team, principal, Action.READ, ResourceType.TEAM))
        .where(Team.id == team_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_departments(
    session: Session,
    principal: Principal,
    *,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> tuple[list[Department], str | None]:
    statement = select(Department).where(
        _readable(Department, principal, Action.LIST, ResourceType.DEPARTMENT),
        Department.status == "active",
    )
    return _page(session, statement, Department, limit, cursor)


def get_department(
    session: Session, principal: Principal, department_id: uuid.UUID
) -> Department | None:
    return session.execute(
        select(Department)
        .where(_readable(Department, principal, Action.READ, ResourceType.DEPARTMENT))
        .where(Department.id == department_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _page(
    session: Session, statement: Any, model: type[Any], limit: int, cursor: Cursor | None
) -> tuple[list[Any], str | None]:
    """The same keyset paging the Work Core uses, over `(created_at, id)`."""
    if cursor is not None:
        statement = statement.where(
            or_(
                model.created_at > cursor.created_at,
                and_(model.created_at == cursor.created_at, model.id > cursor.id),
            )
        )
    rows = list(
        session.execute(
            statement.order_by(model.created_at, model.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        ).scalars()
    )
    if len(rows) > limit:
        last = rows[limit - 1]
        return rows[:limit], encode_cursor(last.created_at, last.id)
    return rows, None


# --------------------------------------------------------------------------- attribution (PQ-7)


@dataclasses.dataclass(frozen=True, slots=True)
class AttributedIdentity:
    """A mapping strong enough to say who someone is. Never a row, never a Person."""

    person_id: uuid.UUID
    confidence: int


def resolve_attribution(
    session: Session, *, org_id: uuid.UUID, source_system: str, external_id: str
) -> AttributedIdentity | None:
    """The Person a channel identifier may be attributed to, or nobody (ADR-0054, BR-I-06).

    Lookup-only, and by the tuple that is already unique — `(org_id, source_system, external_id)`.
    Nothing is created, nothing is inferred from a name, and `handle` is deliberately not matched:
    it carries no uniqueness constraint, so two people could answer to one participant and the
    tie-break would have to be invented.

    A row that exists is not an answer. `may_attribute` decides, and a mapping that is unconfirmed
    or weak returns `None` — which the caller must read as "keep the handle and resolve nobody",
    not as "no such identity". A weak mapping is a legitimate row that may suggest; what it may not
    do is decide.

    `org_id` is filtered explicitly even though RLS already scopes the table, for the reason every
    query here does: two independent controls, neither of which is the excuse for dropping the
    other (BR-G-01a).
    """
    probe = external_id.strip()
    if not probe:
        return None
    row = session.execute(
        select(ExternalIdentity)
        .where(
            ExternalIdentity.org_id == org_id,
            ExternalIdentity.source_system == source_system,
            ExternalIdentity.external_id == probe,
        )
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        return None
    if not may_attribute(confidence=row.confidence, confirmed_at=row.confirmed_at):
        return None
    return AttributedIdentity(person_id=row.person_id, confidence=row.confidence)
