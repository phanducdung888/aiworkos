"""Reading and writing the organization's autonomy policy (ADR-0047, BR-AI-30).

The evaluation itself is in `app.platform.authz.agent` and is a pure function over rows. This module
is the storage: loading a policy, and recording a change to one with an audit entry naming who made
it — which is what BR-AI-32's promotion process needs to point at.

There is no seed and no default row anywhere in this module. An organization with no rows has every
capability off, and that is the state a newly created organization is in.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, Text, func, select, update
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.platform.actor import Actor
from app.platform.audit import record_audit
from app.platform.authz import Action, Decision, Principal, ResourceType
from app.platform.authz.agent import (
    AgentCapability,
    AutonomyMode,
    CapabilityPolicy,
    PolicyCell,
)
from app.platform.db import Base
from app.platform.errors import DomainRuleViolation
from app.platform.ids import uuid7

_TS = TIMESTAMP(timezone=True)


class AgentCapabilityPolicy(Base):
    __tablename__ = "agent_capability_policy"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "decided_by_person_id"],
            ["person.org_id", "person.id"],
            name="agent_policy_decided_by_fk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    #: Why this was decided. Optional, and the thing a reviewer actually wants six months later.
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


def load(session: Session, *, org_id: uuid.UUID) -> CapabilityPolicy:
    """This organization's policy. An organization with no rows denies everything."""
    rows = session.scalars(
        select(AgentCapabilityPolicy).where(AgentCapabilityPolicy.org_id == org_id)
    ).all()
    cells = []
    for row in rows:
        try:
            cells.append(
                PolicyCell(
                    capability=AgentCapability(row.capability),
                    entity_type=row.entity_type,
                    action=Action(row.action),
                    mode=AutonomyMode(row.mode),
                )
            )
        except ValueError:
            # A capability, action or mode this build does not know. Skipped rather than fatal, and
            # skipping means denial — the same direction the absent-row case fails in. A migration
            # that adds a value before the code learns it must not grant anything in that window.
            continue
    return CapabilityPolicy(cells=tuple(cells))


def rows_for(
    session: Session, *, org_id: uuid.UUID
) -> Sequence[AgentCapabilityPolicy]:
    return session.scalars(
        select(AgentCapabilityPolicy)
        .where(AgentCapabilityPolicy.org_id == org_id)
        .order_by(
            AgentCapabilityPolicy.capability,
            AgentCapabilityPolicy.entity_type,
            AgentCapabilityPolicy.action,
        )
    ).all()


def set_mode(
    session: Session,
    *,
    principal: Principal,
    actor: Actor,
    decision: Decision,
    capability: AgentCapability,
    entity_type: str,
    action: Action,
    mode: AutonomyMode,
    reason: str | None = None,
) -> AgentCapabilityPolicy:
    """Set one cell, and audit who set it.

    An agent can never reach this: `AGENT_CAPABILITY_POLICY` is not in the tool registry, there is
    no published service for it, and `app.agent` cannot import this module. An agent that could
    widen its own policy would make every other control here advisory.
    """
    if actor.type.value == "ai":
        raise DomainRuleViolation(
            "BR-AI-23", "an agent may not change its own capability policy"
        )

    existing = session.scalars(
        select(AgentCapabilityPolicy).where(
            AgentCapabilityPolicy.org_id == principal.org_id,
            AgentCapabilityPolicy.capability == capability.value,
            AgentCapabilityPolicy.entity_type == entity_type,
            AgentCapabilityPolicy.action == action.value,
        )
    ).one_or_none()

    before: dict[str, Any] | None = None
    if existing is None:
        row = AgentCapabilityPolicy(
            id=uuid7(),
            org_id=principal.org_id,
            capability=capability.value,
            entity_type=entity_type,
            action=action.value,
            mode=mode.value,
            reason=reason,
            decided_by_person_id=actor.person_id,
        )
        session.add(row)
        session.flush()
    else:
        before = _snapshot(existing)
        session.execute(
            update(AgentCapabilityPolicy)
            .where(AgentCapabilityPolicy.id == existing.id)
            .values(
                mode=mode.value,
                reason=reason,
                decided_by_person_id=actor.person_id,
                updated_at=dt.datetime.now(dt.UTC),
                version=AgentCapabilityPolicy.version + 1,
            )
        )
        session.expire_all()
        refreshed = session.get(AgentCapabilityPolicy, existing.id)
        assert refreshed is not None  # the update above just succeeded
        row = refreshed

    record_audit(
        session,
        org_id=principal.org_id,
        actor=actor,
        action=Action.UPDATE,
        resource_type=ResourceType.AGENT_CAPABILITY_POLICY,
        resource_id=row.id,
        before=before,
        after=_snapshot(row),
        decision=decision,
    )
    return row


def _snapshot(row: AgentCapabilityPolicy) -> dict[str, Any]:
    return {
        "capability": row.capability,
        "entity_type": row.entity_type,
        "action": row.action,
        "mode": row.mode,
        "reason": row.reason,
        "decided_by_person_id": (
            str(row.decided_by_person_id) if row.decided_by_person_id else None
        ),
    }
