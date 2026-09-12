"""Optimistic concurrency (BR-G-06).

A write carries the version the caller believed it was editing. If the row has moved on, the write
is refused and the caller re-reads. Nothing is merged, and no last-writer-wins path exists: two
people editing the same Work is normal, and silently discarding one of their edits is the failure
mode this rule exists to prevent.

The check lives in the `UPDATE ... WHERE version = :expected` statement rather than in a read
followed by a write, so there is no window between deciding and acting.
"""

from __future__ import annotations

import uuid


class StaleVersionError(RuntimeError):
    """The row changed between the caller reading it and attempting to write."""

    def __init__(self, resource: str, resource_id: uuid.UUID, expected: int) -> None:
        super().__init__(
            f"{resource} {resource_id} has changed since version {expected} was read; "
            "re-read it and reapply the change"
        )
        self.resource = resource
        self.resource_id = resource_id
        self.expected = expected
