"""Object storage (ADR-0039).

The architecture already decided where attachment bytes live — MinIO, never Postgres — and that the
API issues short-lived presigned URLs after authorizing the request rather than proxying the bytes
itself. This package is the seam to that store: a protocol the application depends on, an S3 adapter
that implements it, and an in-memory adapter so the capture path is testable without infrastructure,
the same way `test_realm()` makes OIDC testable without running Keycloak.

Nothing here knows what an Event is. The store is handed a key and asked for a URL; deciding whether
the caller may have that URL happened before the call, in the service that loaded the Event.
"""

from app.platform.storage.keys import object_key_for
from app.platform.storage.memory import InMemoryObjectStore
from app.platform.storage.port import ObjectStore, StoredObject
from app.platform.storage.s3 import S3ObjectStore

__all__ = [
    "InMemoryObjectStore",
    "ObjectStore",
    "S3ObjectStore",
    "StoredObject",
    "object_key_for",
]
