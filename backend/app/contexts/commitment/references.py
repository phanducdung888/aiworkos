"""Reference predicate for other contexts (ADR-0035).

A Proposal may target an existing Commitment. Existence in this organization, nothing more.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contexts.commitment.models import Commitment
from app.platform.errors import DomainRuleViolation


def assert_commitment_exists(
    session: Session, *, org_id: uuid.UUID, commitment_id: uuid.UUID, field: str = "commitment"
) -> None:
    exists = session.scalars(
        select(Commitment.id).where(
            Commitment.org_id == org_id, Commitment.id == commitment_id
        )
    ).first()
    if exists is None:
        raise DomainRuleViolation(
            "BR-G-01", f"{field} does not name a commitment in this organization"
        )
