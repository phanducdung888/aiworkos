"""Reference predicates for other contexts (ADR-0035).

A Project names an owning Team and a lead Person; a Work Core service must be able to refuse a
reference that does not resolve *before* it writes, and it must do so without reading Identity's
tables. This is that interface, and it is deliberately tiny: three questions, each answered yes or
no, none of which leaks a row.

They raise rather than return a boolean. The caller is always about to refuse the write anyway, and
raising here means the error names the field and the rule — `BR-G-01: no such team in this
organization` — instead of a composite foreign key violation arriving from psycopg several layers
later (W-11).

Scoped to the calling organization by construction. "Does team X exist" can only ever mean "does it
exist here", which is also why a caller cannot use this to probe another tenant.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.contexts.identity import repository
from app.platform.errors import DomainRuleViolation


def assert_person_exists(
    session: Session, *, org_id: uuid.UUID, person_id: uuid.UUID, field: str = "person"
) -> None:
    if not repository.person_exists(session, org_id=org_id, person_id=person_id):
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name a person in this organization"
        )


def assert_team_exists(
    session: Session, *, org_id: uuid.UUID, team_id: uuid.UUID, field: str = "team"
) -> None:
    if not repository.team_exists(session, org_id=org_id, team_id=team_id):
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name a team in this organization"
        )


def assert_department_exists(
    session: Session,
    *,
    org_id: uuid.UUID,
    department_id: uuid.UUID,
    field: str = "department",
) -> None:
    if not repository.department_exists(session, org_id=org_id, department_id=department_id):
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name a department in this organization"
        )
