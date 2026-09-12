"""Cursor pagination.

Keyset, not offset. `OFFSET 5000` makes the database walk five thousand rows it will discard, and
it skips or repeats rows whenever something is inserted between two page requests. A cursor naming
the last row seen has neither problem.

The cursor is the sort key `(created_at, id)` — the full key, because `created_at` alone is not
unique and a tie at a page boundary would drop or duplicate a row. It is base64url-encoded so that
clients treat it as opaque: the day a client parses one is the day the sort key can never change.

No `total`. Counting the whole table on every page is expensive, and a count is an inference
channel: the number of rows a principal cannot see is exactly what a count that ignores
authorization would report (contract §5).
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import uuid
from dataclasses import dataclass

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class InvalidCursor(Exception):
    """The cursor was not one we issued."""


@dataclass(frozen=True, slots=True)
class Cursor:
    created_at: dt.datetime
    id: uuid.UUID


def clamp_limit(requested: int | None) -> int:
    """Out-of-range asks are clamped, not refused.

    A client asking for 5000 rows wants as many as it can have, and failing the request teaches it
    nothing it could not learn from receiving 200.
    """
    if requested is None:
        return DEFAULT_LIMIT
    return max(1, min(requested, MAX_LIMIT))


def encode_cursor(created_at: dt.datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> Cursor:
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + padding).decode()
        timestamp, _, row_id = raw.partition("|")
        return Cursor(created_at=dt.datetime.fromisoformat(timestamp), id=uuid.UUID(row_id))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor("the cursor is not one this API issued") from exc
