"""Audit infrastructure.

An audit entry is written in the same transaction as the change it describes (BR-G-02). If the
business mutation rolls back, so does its audit entry; there is no such thing as an audit record for
something that did not happen, and no such thing as a change without one.

Append-only is enforced in the database by a trigger, not by convention here. This module is the
only supported way to write an entry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.actor import Actor
from app.platform.authz.model import Action, Decision, ResourceType
from app.platform.ids import uuid7

_INSERT = text(
    """
    INSERT INTO audit_entry (
        id, org_id, occurred_at, actor, action, resource_type, resource_id,
        before_state, after_state, request_id, authorization_context
    ) VALUES (
        :id, :org_id, now(), CAST(:actor AS jsonb), :action, :resource_type, :resource_id,
        CAST(:before_state AS jsonb), CAST(:after_state AS jsonb), :request_id,
        CAST(:authorization_context AS jsonb)
    )
    RETURNING id
    """
)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: uuid.UUID
    org_id: uuid.UUID
    action: str
    resource_type: str
    resource_id: uuid.UUID | None


def record_audit(
    session: Session,
    *,
    org_id: uuid.UUID,
    actor: Actor,
    action: Action | str,
    resource_type: ResourceType | str,
    resource_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    decision: Decision | None = None,
) -> AuditRecord:
    """Append one audit entry. Must be called inside the mutating transaction."""
    import json

    action_value = action.value if isinstance(action, Action) else str(action)
    resource_value = (
        resource_type.value if isinstance(resource_type, ResourceType) else str(resource_type)
    )
    entry_id = uuid7()
    session.execute(
        _INSERT,
        {
            "id": entry_id,
            "org_id": org_id,
            "actor": json.dumps(actor.to_json()),
            "action": action_value,
            "resource_type": resource_value,
            "resource_id": resource_id,
            "before_state": json.dumps(before) if before is not None else None,
            "after_state": json.dumps(after) if after is not None else None,
            "request_id": actor.request_id,
            "authorization_context": json.dumps(decision.to_json()) if decision else None,
        },
    )
    return AuditRecord(entry_id, org_id, action_value, resource_value, resource_id)


class AuditImmutabilityError(RuntimeError):
    """Raised by the database trigger path when something tries to alter history."""
