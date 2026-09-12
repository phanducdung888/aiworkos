"""Error types every context raises.

Cross-cutting, so they live here rather than in one context that the others would then have to
import. `StaleVersionError` in `platform/concurrency` is the same idea: a rule can be a business
rule and still belong to the platform, because what makes it platform-level is that every context
expresses it identically.

Placing them here is also what lets the HTTP layer translate them without importing upward. An
exception handler that had to know about `contexts.work` would have to know about every context
eventually, and platform sits underneath all of them (ADR-0001).
"""

from __future__ import annotations

import uuid


class DomainRuleViolation(ValueError):
    """A business rule was broken. Carries the rule id so the message is traceable to the spec."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule


class EntityNotFound(LookupError):
    """The entity does not exist, or does not exist in this organization.

    Deliberately one error rather than two. "Exists but belongs to someone else" is itself a fact
    about another organization, and leaking it through a distinguishable error would defeat the
    isolation the rest of the system works to maintain (PQ-2, contract §5).
    """

    def __init__(self, resource: str, resource_id: uuid.UUID) -> None:
        super().__init__(f"{resource} {resource_id} not found")
        self.resource = resource
        self.resource_id = resource_id


class TerminalJobError(Exception):
    """A job failure that retrying cannot fix.

    The queue's default is to retry, which is right for a transient fault and wrong for a state
    that will never change. An approval past its execution window is the latter: it cannot become
    unexpired, so the job goes to `dead` immediately rather than burning five attempts against it
    (ADR-0051).
    """


class UpstreamProviderError(Exception):
    """A dependency outside this system failed.

    Rendered as 502 or 504 rather than 500: "the model provider is unreachable" and "this service
    has a bug" are different facts, and a client that cannot tell them apart cannot decide whether
    retrying is sensible. The AI interaction is still recorded — the run happened, it just did not
    succeed.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
