"""Reference predicates for other contexts (ADR-0035, same shape as Identity's).

A Commitment may name the Work that fulfils it (BR-C-08) and a Proposal may target one. Both need to
refuse a reference that does not resolve before writing, and neither may read the Work Core's tables
to find out.

Deliberately not visibility-filtered. This answers "does this Work exist in this
organization", which is what a foreign key would answer and is all a reference check is
entitled to know. Whether the
caller may *read* that Work is a different question, answered by the read path; conflating them here
would leak visibility through a validation error.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contexts.work.models import Project, Work
from app.platform.errors import DomainRuleViolation


def assert_work_exists(
    session: Session, *, org_id: uuid.UUID, work_id: uuid.UUID, field: str = "work"
) -> None:
    exists = session.scalars(
        select(Work.id).where(Work.org_id == org_id, Work.id == work_id)
    ).first()
    if exists is None:
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name a work item in this organization"
        )


def assert_project_exists(
    session: Session, *, org_id: uuid.UUID, project_id: uuid.UUID, field: str = "project"
) -> None:
    """The same guard for a Project, and for the same reason (CP27).

    A composite foreign key already refuses a dangling or cross-tenant reference, so this changes
    nothing about what can be stored. What it changes is how the refusal reads: an integrity error
    surfacing from the database is a 500 and a stack trace, and a caller supplying an identifier —
    now including an approved AI action carrying `project_id` — is entitled to be told which field
    was wrong.
    """
    exists = session.scalars(
        select(Project.id).where(Project.org_id == org_id, Project.id == project_id)
    ).first()
    if exists is None:
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name a project in this organization"
        )
