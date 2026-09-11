"""Time-ordered identifiers.

UUIDv7 (RFC 9562) is generated in the application rather than the database so that ids are known
before a flush and so that ordering is stable across services. PostgreSQL 16 has no native
`uuidv7()`; tables keep `gen_random_uuid()` as a server-side fallback for rows created outside the
application (fixtures, ops scripts).
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a UUIDv7: 48-bit big-endian Unix epoch milliseconds, then random bits."""
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand = bytearray(os.urandom(10))
    value = ms.to_bytes(6, "big") + bytes(rand)
    b = bytearray(value)
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(b))
