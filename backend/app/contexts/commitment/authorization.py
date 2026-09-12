"""Relations for Commitment (BR-C-09).

The rule names three people: the committer, the recipient, and a lead in their management chain.
The first two are `PERSONAL`; the third is what the department and team grants already express, so
the reach computation the Work Core built is reused rather than reimplemented.
"""

from __future__ import annotations

import uuid

from app.contexts.commitment.domain import Authority
from app.contexts.commitment.models import Commitment
from app.platform.authz import Relation, ResourceType
from app.platform.authz.model import ResourceRef


def ref(
    resource_type: ResourceType,
    org_id: uuid.UUID,
    relations: frozenset[Relation],
    resource_id: uuid.UUID | None = None,
) -> ResourceRef:
    return ResourceRef(type=resource_type, org_id=org_id, id=resource_id, relations=relations)


def authority_of(
    commitment: Commitment,
    *,
    actor_person_id: uuid.UUID | None,
    leads_team_ids: frozenset[uuid.UUID],
    is_system: bool = False,
) -> Authority:
    """Who this actor is relative to this promise.

    The lead test is deliberately about the *committer's* team rather than the recipient's: BR-C-09
    says a lead in their management chain, and the person whose accountability is at stake is the
    one who made the promise.
    """
    return Authority(
        is_committer=actor_person_id is not None
        and commitment.committed_by_person_id == actor_person_id,
        is_recipient=actor_person_id is not None
        and commitment.committed_to_person_id == actor_person_id,
        is_lead_in_chain=commitment.committed_to_team_id is not None
        and commitment.committed_to_team_id in leads_team_ids,
        is_system=is_system,
    )


def commitment_relations(
    commitment: Commitment, *, actor_person_id: uuid.UUID | None
) -> frozenset[Relation]:
    if actor_person_id is not None and actor_person_id in (
        commitment.committed_by_person_id,
        commitment.committed_to_person_id,
    ):
        return frozenset({Relation.PERSONAL})
    return frozenset()
