"""Object keys are derived, never accepted.

A client that could name the key could ask for a presigned URL to somebody else's object, and the
authorization that ran against the Event would be beside the point. So the key is a pure function of
identifiers the server already holds, and there is no code path that takes one from a request.

The organization comes first so that a bucket listing is segregated by tenant and a lifecycle or
replication rule can be written per organization without parsing anything.
"""

from __future__ import annotations

import uuid


def object_key_for(*, org_id: uuid.UUID, event_id: uuid.UUID, attachment_id: uuid.UUID) -> str:
    return f"org/{org_id}/event/{event_id}/attachment/{attachment_id}"
