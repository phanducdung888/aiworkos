"""The proposed action and its canonical hash (ADR-0041).

An action is a tool name, a tool version and fully resolved arguments: everything needed to perform
the mutation, with nothing left to infer at execution time. That completeness is the point. A human
approves *this*, and the Tool Gateway later executes *this*, and the two are the same bytes.

Canonicalisation is load-bearing and therefore fixed here rather than left to whatever `json.dumps`
does by default. Sorted keys, no insignificant whitespace, UUIDs and dates as strings: two documents
that differ only in key order are the same action, and two that differ in a nested value are not.
Change this function and every stored approval silently stops matching — which is why it has its own
tests and why the format is spelled out rather than implied.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from app.platform.errors import DomainRuleViolation


def _canonical(value: Any) -> Any:
    """Render a value in a form that is stable across processes and Python versions.

    `uuid` and the date types are converted rather than rejected because an action's arguments are
    naturally full of them, and a caller should not have to stringify by hand and risk doing it
    differently from the next caller.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        # Floats do not round-trip identically through JSON in every runtime, so an action whose
        # hash depended on one would be a hash that occasionally disagrees with itself. No argument
        # in this system needs one; refusing is safer than producing an unstable digest.
        raise DomainRuleViolation(
            "ADR-0041", "action arguments may not contain floating-point values"
        )
    raise DomainRuleViolation(
        "ADR-0041", f"action arguments may not contain {type(value).__name__}"
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Action:
    """A fully resolved call to one registered tool."""

    tool: str
    tool_version: str
    arguments: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "tool_version": self.tool_version,
            "arguments": _canonical(self.arguments),
        }

    def canonical_form(self) -> str:
        return json.dumps(
            self.as_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_form().encode()).hexdigest()

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Action:
        """Rebuild an Action from a stored row.

        Used by the Tool Gateway to recompute the hash of what was approved. It goes through the
        same construction path as a freshly built Action, so a round trip through the database
        cannot change the digest — if it could, every approval would expire the moment it was read
        back.
        """
        for field in ("tool", "tool_version", "arguments"):
            if field not in payload:
                raise DomainRuleViolation(
                    "ADR-0041", f"a stored action is missing {field}"
                )
        arguments = payload["arguments"]
        if not isinstance(arguments, dict):
            raise DomainRuleViolation("ADR-0041", "action arguments must be an object")
        return cls(
            tool=str(payload["tool"]),
            tool_version=str(payload["tool_version"]),
            arguments=arguments,
        )


def hash_of(payload: dict[str, Any]) -> str:
    """The digest of a stored action document."""
    return Action.from_json(payload).digest()
