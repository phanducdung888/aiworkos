"""Relations for Signal/Capture.

An Event has no owning team and no project, so almost none of the Work Core's relation machinery
applies. What it has is participants and a capturer, and those are the two relations that matter:

* the person who captured it keeps a `PERSONAL` relation, which is what lets a member attach a file
  to the Event they just recorded without giving them that power over everybody else's;
* being named as a participant is what BR-E-08 turns into read access on a `restricted` Event, and
  that one is applied as a query predicate rather than a grant, because the policy engine is not
  allowed to read the participant table.
"""

from __future__ import annotations

import uuid

from app.contexts.signal.models import Event
from app.platform.authz import Relation, ResourceType
from app.platform.authz.model import ResourceRef


def ref(
    resource_type: ResourceType,
    org_id: uuid.UUID,
    relations: frozenset[Relation],
    resource_id: uuid.UUID | None = None,
) -> ResourceRef:
    return ResourceRef(
        type=resource_type, org_id=org_id, id=resource_id, relations=relations
    )


def capture_relations() -> frozenset[Relation]:
    """Capturing is not related to anything yet — the Event does not exist.

    `SAME_ORG` is added by the policy engine for every request, so an empty set here means "judge
    this on the role's organization-wide grant alone", which is exactly what capture is.
    """
    return frozenset()


def event_relations(event: Event, *, actor_person_id: uuid.UUID | None) -> frozenset[Relation]:
    """The relation a person has to an Event they wrote down.

    Deliberately not extended to participants. Being named in a message is not authority over the
    record of it — a participant's access to a `restricted` Event is a read, granted by BR-E-08 in
    the query layer, and turning it into a `PERSONAL` relation here would silently hand them the
    `ATTACH` grant as well.
    """
    if actor_person_id is not None and event.captured_by_person_id == actor_person_id:
        return frozenset({Relation.PERSONAL})
    return frozenset()
