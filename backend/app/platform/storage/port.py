"""The port. Four operations, and deliberately no fifth.

There is no `put` and no `get`: bytes do not pass through this process (ADR-0039). A caller asks for
a URL and hands it to whoever is doing the transfer. That constraint is expressed here, in the shape
of the protocol, rather than in a comment somewhere asking people not to.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Protocol


@dataclasses.dataclass(frozen=True, slots=True)
class StoredObject:
    """What the store reports about an object it holds.

    These are the numbers that get recorded, never the client's claimed ones: a presigned PUT is a
    window in which the client writes whatever it likes, so the only trustworthy size and checksum
    are the ones read back afterwards.
    """

    key: str
    size_bytes: int
    #: The store's entity tag. For a single-part upload MinIO and S3 both return the MD5 of the
    #: content; for a multipart upload they return a composite that is not an MD5 at all. It is
    #: recorded as an opaque integrity token for exactly that reason — it detects a changed object,
    #: and it is not presented anywhere as a content hash.
    etag: str
    media_type: str | None = None


class ObjectStore(Protocol):
    """What the application is allowed to ask of the object store."""

    def presigned_put(self, key: str, *, media_type: str, expires_in: dt.timedelta) -> str:
        """A URL the holder may use to write `key`, once, until it expires."""
        ...

    def presigned_get(self, key: str, *, expires_in: dt.timedelta) -> str:
        """A URL the holder may use to read `key` until it expires."""
        ...

    def stat(self, key: str) -> StoredObject | None:
        """What the store holds at `key`, or None if the upload never happened."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object. Used by retention (BR-E-07), never by a request handler."""
        ...
