"""An in-memory `ObjectStore` for tests.

It is a real implementation of the protocol rather than a mock: `stat` answers about objects that
were actually written through the URLs it issued, so a test that forgets the upload step sees the
same `pending` attachment a real client would leave behind. A mock returning a canned size would
make that bug untestable, which is the bug most worth having a test for.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from app.platform.storage.port import StoredObject


class InMemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str | None]] = {}
        #: Issued URL -> the key it authorises. A test uploads by calling `write_through`, so the
        #: URL is the only way in and an unissued key cannot be written.
        self._puts: dict[str, str] = {}
        self._gets: dict[str, str] = {}

    # -------------------------------------------------------------- ObjectStore

    def presigned_put(self, key: str, *, media_type: str, expires_in: dt.timedelta) -> str:
        url = f"https://objects.test/put/{uuid.uuid4().hex}"
        self._puts[url] = key
        return url

    def presigned_get(self, key: str, *, expires_in: dt.timedelta) -> str:
        url = f"https://objects.test/get/{uuid.uuid4().hex}"
        self._gets[url] = key
        return url

    def stat(self, key: str) -> StoredObject | None:
        stored = self._objects.get(key)
        if stored is None:
            return None
        content, media_type = stored
        return StoredObject(
            key=key,
            size_bytes=len(content),
            etag=hashlib.md5(content, usedforsecurity=False).hexdigest(),
            media_type=media_type,
        )

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    # -------------------------------------------------------------- test affordances

    def write_through(self, url: str, content: bytes, media_type: str | None = None) -> None:
        """Upload, the way a client would: through an issued URL, not by naming a key."""
        key = self._puts.get(url)
        if key is None:
            raise AssertionError(f"no presigned PUT was issued for {url}")
        self._objects[key] = (content, media_type)

    def key_for_get(self, url: str) -> str | None:
        return self._gets.get(url)

    def contains(self, key: str) -> bool:
        return key in self._objects
