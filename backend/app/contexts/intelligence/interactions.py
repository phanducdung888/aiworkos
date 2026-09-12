"""Recording AI runs and the calls they made (BR-AI-02, BR-AI-03, BR-PR-08).

An AIInteraction is opened before the run does anything and closed when it ends, which is the
ordering BR-AI-02 forces: an AI-originated Proposal must carry an interaction id, so the interaction
has to exist before the Proposal can. A run that produced output and left no record of itself is the
gap BR-PR-08 exists to close.

Nothing here persists a credential, a prompt body or a raw model response. `input_refs` holds
identifiers; `arguments_redacted` holds argument *names*. The reason is not only secrecy: an audit
table that accumulated a second copy of every message would have a retention story of its own, and
BR-E-07's purge would leave it behind.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.contexts.intelligence.models import AIInteraction, ToolCall
from app.platform.errors import DomainRuleViolation
from app.platform.ids import uuid7

#: Field names that must never reach a persisted column, whatever a caller passes.
#:
#: A denylist is a weak control on its own and this is not the only one — the schema has no column
#: for any of these. It exists because `input_refs` and `arguments_redacted` are `jsonb`, so the
#: schema cannot refuse an unexpected key, and a caller building a dict from a config object would
#: otherwise sweep one in without noticing.
FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "private_key",
        "session",
        "cookie",
    }
)


def assert_no_secrets(payload: dict[str, Any], *, field: str) -> None:
    """Refuse anything that looks like a credential, at any depth.

    Raising rather than redacting: a caller that tried to store a secret has a bug, and quietly
    dropping the key would leave them believing it was recorded.
    """
    stack: list[Any] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if str(key).strip().lower().replace("-", "_") in FORBIDDEN_KEYS:
                    raise DomainRuleViolation(
                        "BR-AI-26", f"{field} may not contain {key}"
                    )
                stack.append(value)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)


@dataclasses.dataclass(frozen=True, slots=True)
class StartInteraction:
    kind: str
    trigger_type: str
    agent_identity: str
    runtime: str
    provider: str
    prompt_id: str
    prompt_version: str
    tool_manifest_version: str
    trigger_ref: uuid.UUID | None = None
    principal_person_id: uuid.UUID | None = None
    model: str = ""
    model_version: str = ""
    input_refs: dict[str, Any] = dataclasses.field(default_factory=dict)
    correlation_id: str | None = None


def start(session: Session, *, org_id: uuid.UUID, command: StartInteraction) -> AIInteraction:
    if command.prompt_version.strip() in ("", "latest"):
        # Also a check constraint. Stated here so the caller gets the reason rather than a
        # constraint name: a run that cannot say which prompt produced it is not reproducible.
        raise DomainRuleViolation(
            "BR-AI-02", "prompt_version must be pinned, never 'latest'"
        )
    assert_no_secrets(command.input_refs, field="input_refs")

    interaction = AIInteraction(
        id=uuid7(),
        org_id=org_id,
        kind=command.kind,
        trigger_type=command.trigger_type,
        trigger_ref=command.trigger_ref,
        principal_person_id=command.principal_person_id,
        agent_identity=command.agent_identity,
        runtime=command.runtime,
        provider=command.provider,
        model=command.model,
        model_version=command.model_version,
        prompt_id=command.prompt_id,
        prompt_version=command.prompt_version,
        tool_manifest_version=command.tool_manifest_version,
        input_refs=command.input_refs,
        correlation_id=command.correlation_id,
    )
    session.add(interaction)
    session.flush()
    return interaction


def finish(
    session: Session,
    *,
    org_id: uuid.UUID,
    interaction_id: uuid.UUID,
    status: str,
    finished_at: dt.datetime,
    latency_ms: int | None = None,
    model: str | None = None,
    model_version: str | None = None,
    token_usage: dict[str, Any] | None = None,
    cost_estimate: Decimal | None = None,
    output_summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> AIInteraction:
    """Write the outcome. The only mutation this row ever takes (ADR-0038's trigger allows it).

    `model` and `model_version` are writable here because they are not known until the provider
    answers — a routed request may land on a different model version than the last one did, and
    recording the one that was asked for rather than the one that replied would be a lie about
    which model produced the output.
    """
    if token_usage is not None:
        assert_no_secrets(token_usage, field="token_usage")
    if output_summary is not None:
        assert_no_secrets(output_summary, field="output_summary")

    values: dict[str, Any] = {
        "status": status,
        "finished_at": finished_at,
        "latency_ms": latency_ms,
        "token_usage": token_usage,
        "cost_estimate": cost_estimate,
        "output_summary": output_summary,
        "error": error[:2000] if error else None,
        "version": AIInteraction.version + 1,
    }
    if model is not None:
        values["model"] = model
    if model_version is not None:
        values["model_version"] = model_version

    session.execute(
        update(AIInteraction)
        .where(AIInteraction.org_id == org_id, AIInteraction.id == interaction_id)
        .values(**values)
    )
    session.expire_all()
    updated = get(session, org_id=org_id, interaction_id=interaction_id)
    if updated is None:  # pragma: no cover - the update just succeeded
        raise DomainRuleViolation("BR-AI-02", "the interaction disappeared mid-run")
    return updated


def get(
    session: Session, *, org_id: uuid.UUID, interaction_id: uuid.UUID
) -> AIInteraction | None:
    return session.scalars(
        select(AIInteraction).where(
            AIInteraction.org_id == org_id, AIInteraction.id == interaction_id
        )
    ).one_or_none()


def list_recent(
    session: Session, *, org_id: uuid.UUID, limit: int = 50
) -> Sequence[AIInteraction]:
    return session.scalars(
        select(AIInteraction)
        .where(AIInteraction.org_id == org_id)
        .order_by(AIInteraction.started_at.desc())
        .limit(limit)
    ).all()


def record_call(
    session: Session,
    *,
    org_id: uuid.UUID,
    ai_interaction_id: uuid.UUID,
    sequence: int,
    tool_name: str,
    tool_version: str,
    authorization_result: str,
    outcome: str,
    arguments_redacted: dict[str, Any] | None = None,
    target_entity_type: str | None = None,
    target_entity_id: uuid.UUID | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> ToolCall:
    """Append one call. Denials included — they are the rows worth reading."""
    redacted = arguments_redacted or {}
    assert_no_secrets(redacted, field="arguments_redacted")
    call = ToolCall(
        id=uuid7(),
        org_id=org_id,
        ai_interaction_id=ai_interaction_id,
        sequence=sequence,
        tool_name=tool_name,
        tool_version=tool_version,
        arguments_redacted=redacted,
        authorization_result=authorization_result,
        outcome=outcome,
        target_entity_type=target_entity_type,
        target_entity_id=target_entity_id,
        duration_ms=duration_ms,
        error=error[:2000] if error else None,
    )
    session.add(call)
    session.flush()
    return call


def calls_for(
    session: Session, *, org_id: uuid.UUID, interaction_id: uuid.UUID
) -> Sequence[ToolCall]:
    return session.scalars(
        select(ToolCall)
        .where(ToolCall.org_id == org_id, ToolCall.ai_interaction_id == interaction_id)
        .order_by(ToolCall.sequence)
    ).all()


#: Published aliases. The module-level names are short because they read well next to each other
#: here; the interface names say what they are next to everything else a caller imports.
start_interaction = start
finish_interaction = finish
get_interaction = get
list_interactions = list_recent
record_tool_call = record_call
