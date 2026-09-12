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

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

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
