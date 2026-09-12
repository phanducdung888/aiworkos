"""The published interface of the Commitment context (ADR-0001, ADR-0040)."""

from app.contexts.commitment.authorization import authority_of, commitment_relations
from app.contexts.commitment.commands import (
    ChangeCommitmentStatus,
    CreateCommitment,
    UpdateCommitment,
)
from app.contexts.commitment.domain import (
    Authority,
    CommitmentStatus,
    DuePrecision,
    is_missed,
)
from app.contexts.commitment.models import Commitment
from app.contexts.commitment.queries import (
    CommitmentFilter,
    CommitmentPage,
    get_commitment,
    list_commitments,
)
from app.contexts.commitment.references import assert_commitment_exists
from app.contexts.commitment.services import CommitmentService, ServiceContext
from app.contexts.commitment.similarity import (
    MIN_SIMILARITY,
    SimilarCommitment,
    find_similar_commitments,
)

__all__ = [
    "Authority",
    "MIN_SIMILARITY",
    "SimilarCommitment",
    "ChangeCommitmentStatus",
    "Commitment",
    "CommitmentFilter",
    "CommitmentPage",
    "CommitmentService",
    "CommitmentStatus",
    "CreateCommitment",
    "DuePrecision",
    "ServiceContext",
    "UpdateCommitment",
    "assert_commitment_exists",
    "authority_of",
    "commitment_relations",
    "find_similar_commitments",
    "get_commitment",
    "is_missed",
    "list_commitments",
]
