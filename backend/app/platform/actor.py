"""Who caused a change.

Every mutation in the system resolves to one of these. There is no unattributed write path
(BR-G-02). `ai_interaction_id` and `approval_record_id` are declared now and unused in Phase 1:
they are the fields that make an AI-originated mutation explainable later, and adding them after
the audit table is in production would be a migration nobody wants.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class ActorType(enum.StrEnum):
    PERSON = "person"
    SYSTEM = "system"
    AI = "ai"


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    person_id: uuid.UUID | None = None
    service_account: str | None = None
    ai_interaction_id: uuid.UUID | None = None
    approval_record_id: uuid.UUID | None = None
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type is ActorType.PERSON and self.person_id is None:
            raise ValueError("a person actor requires person_id")
        if self.type is ActorType.AI:
            if self.ai_interaction_id is None:
                raise ValueError("an AI actor requires ai_interaction_id (BR-AI-02)")
            if self.person_id is None:
                raise ValueError("an AI actor requires a delegated principal (BR-AI-03)")

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "person_id": str(self.person_id) if self.person_id else None,
            "service_account": self.service_account,
            "ai_interaction_id": str(self.ai_interaction_id) if self.ai_interaction_id else None,
            "approval_record_id": (
                str(self.approval_record_id) if self.approval_record_id else None
            ),
            "request_id": self.request_id,
            **({"extra": self.extra} if self.extra else {}),
        }


def system_actor(reason: str, request_id: str | None = None) -> Actor:
    """The actor used by monitors and internal deterministic actions (BR-AI-28)."""
    return Actor(type=ActorType.SYSTEM, service_account="workos.system",
                 request_id=request_id, extra={"reason": reason})
