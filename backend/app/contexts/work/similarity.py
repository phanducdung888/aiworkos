"""`find_similar` for Work (BR-AI-05).

The rule: before proposing a new Work item, the AI must search for one that already exists.
Proposing without a prior search in the same interaction is rejected. The failure it prevents is
mundane and corrosive — an agent that reads ten messages about one deadline proposes ten Work items,
somebody has to reject nine, and they learn to stop reading proposals carefully.

**Deterministic PostgreSQL, not a vector database.** Trigram similarity over titles, ordered by
score then id, so the same corpus and the same query always produce the same list in the same
order. That matters more here than recall does: BR-AI-05 asks "did you look", and an answer that
varies between runs makes the check unreproducible and the evaluation metrics meaningless. A vector
index is a real improvement to *quality* and is not needed to make the rule enforceable, so it is
not being introduced on speculation.

**Reads through the caller's own visibility.** The search runs on `readable_work`, the same query
the read API uses, so an agent cannot discover the existence of Work its delegating person cannot
see. A similarity search is an excellent way to leak a corpus one probe at a time, and the defence
is not to filter the results afterwards but to never select the rows.

**Returns references, not content.** An id, a title, a status and a score. Enough for a reviewer to
click through and for the agent to decide whether to propose at all; not a copy of the description,
because a search result is not a grant of access to everything the row holds.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import Float, func
from sqlalchemy.orm import Session

from app.contexts.work.authorization import ActorReach
from app.contexts.work.models import Work
from app.contexts.work.queries import readable_work
from app.platform.authz import Principal

#: Below this, two titles are not the same piece of work in any useful sense. Chosen so that
#: "Send the revised quote" matches "Send revised quote to finance" and does not match "Book the
#: venue" — tuned by the tests below rather than by a number that felt right.
MIN_SIMILARITY = 0.30

#: A search returns few results on purpose. The question is "does this already exist", and twenty
#: weak matches answer it worse than three strong ones.
MAX_RESULTS = 5


@dataclasses.dataclass(frozen=True, slots=True)
class SimilarWork:
    """A reference, not a copy."""

    id: uuid.UUID
    title: str
    status: str
    score: float


def find_similar_work(
    session: Session,
    principal: Principal,
    reach: ActorReach,
    *,
    title: str,
    limit: int = MAX_RESULTS,
) -> list[SimilarWork]:
    """Work this principal can see whose title resembles `title`.

    Ordering is `(score desc, id)`. The id tiebreak is what makes the result reproducible: trigram
    scores collide often on short titles, and without it the order would depend on the plan.
    """
    probe = title.strip()
    if not probe:
        return []

    score = func.similarity(Work.title, probe).cast(Float).label("score")
    statement = (
        readable_work(principal, reach)
        .add_columns(score)
        .where(func.similarity(Work.title, probe) >= MIN_SIMILARITY)
        .order_by(score.desc(), Work.id)
        .limit(limit)
    )
    return [
        SimilarWork(
            id=row.Work.id,
            title=row.Work.title,
            status=row.Work.status,
            score=round(float(row.score), 4),
        )
        for row in session.execute(statement).all()
    ]
