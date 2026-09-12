"""`find_similar` for Commitment (BR-AI-05).

This exists because the duplicate check it replaces was a category error. Until CP14 every
accepted intent — commitments included — was deduplicated by searching *Work titles*, which could
both miss a promise the same person had already made and suppress a real commitment because an
unrelated Work item happened to be worded alike.

**A duplicate commitment is a narrower question than a duplicate Work item.** Work is a duplicate of
Work when it describes the same job. A promise is a duplicate when *the same person* has already
promised *the same thing* and that promise is still standing. The committer is therefore part of the
query rather than a filter applied afterwards: two people promising the same deliverable are two
commitments, and collapsing them would erase one person's accountability.

**Deterministic PostgreSQL, for the same reason Work's search is.** Trigram similarity over
statements, ordered by score then id, so the same corpus and query always produce the same list.
BR-AI-05 asks "did you look", and an answer that varies between runs makes the rule unenforceable.

**No index, and that is a recorded limitation rather than an oversight.** `pg_trgm` is installed
(migration 0012) but `commitment.statement` has no trigram index, so this filters the organization's
commitments and scores the survivors. Correct at every size and slow at a large one. Adding
`ix_commitment_statement_trgm` is a migration, which CP14 deliberately does not have; Work's
equivalent index arrived with the migration that introduced its search, and this one should arrive
the same way.

**Visibility needs no narrowing, and that is Commitment's decision, not an omission here.**
`repository.readable` is organization-wide by design (`queries.py`), because the matrix gives every
role an ORG grant on `COMMITMENT.READ`. An agent searching this corpus discovers nothing its
delegating person could not already list.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import Float, func
from sqlalchemy.orm import Session

from app.contexts.commitment import repository
from app.contexts.commitment.domain import CommitmentStatus
from app.contexts.commitment.models import Commitment
from app.platform.authz import Principal

#: Below this, two statements are not the same promise in any useful sense. The same threshold Work
#: uses, deliberately: one number, tuned by tests, rather than two that drift apart.
MIN_SIMILARITY = 0.30

#: A search returns few results on purpose. The question is "does this already exist", and twenty
#: weak matches answer it worse than three strong ones.
MAX_RESULTS = 5

#: Promises that are still standing. A fulfilled, cancelled or withdrawn commitment is finished
#: business, and promising the same thing again afterwards is a new promise rather than a duplicate
#: of an old one.
LIVE_STATUSES = frozenset(
    {
        CommitmentStatus.CAPTURED.value,
        CommitmentStatus.OPEN.value,
        CommitmentStatus.RENEGOTIATED.value,
        CommitmentStatus.MISSED.value,
        CommitmentStatus.DISPUTED.value,
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class SimilarCommitment:
    """A reference, not a copy."""

    id: uuid.UUID
    statement: str
    status: str
    score: float


def find_similar_commitments(
    session: Session,
    principal: Principal,
    *,
    statement: str,
    committed_by_person_id: uuid.UUID,
    limit: int = MAX_RESULTS,
) -> list[SimilarCommitment]:
    """Standing promises by this person whose statement resembles `statement`.

    Ordering is `(score desc, id)`. The id tiebreak is what makes the result reproducible: trigram
    scores collide often on short statements, and without it the order would depend on the plan.
    """
    probe = statement.strip()
    if not probe:
        return []

    score = func.similarity(Commitment.statement, probe).cast(Float).label("score")
    query = (
        repository.readable(principal.org_id)
        .add_columns(score)
        .where(
            Commitment.committed_by_person_id == committed_by_person_id,
            Commitment.status.in_(sorted(LIVE_STATUSES)),
            func.similarity(Commitment.statement, probe) >= MIN_SIMILARITY,
        )
        .order_by(score.desc(), Commitment.id)
        .limit(limit)
    )
    return [
        SimilarCommitment(
            id=row.Commitment.id,
            statement=row.Commitment.statement,
            status=row.Commitment.status,
            score=round(float(row.score), 4),
        )
        for row in session.execute(query).all()
    ]
