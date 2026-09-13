"""A durable job queue in PostgreSQL (ADR-0044).

Claimed with `SELECT ... FOR UPDATE SKIP LOCKED` inside the transaction that does the work, so a
crash at any point leaves the row unclaimed and retryable, and a success leaves it unambiguously
finished. `SKIP LOCKED` is what lets several workers run without a lock service.

Delivery is at-least-once and deliberately so. Exactly-once *execution* is not this module's job and
cannot be: a worker can commit and die before anything observes it. It comes from the handler —
executing an approved Proposal claims the ApprovalRecord with a conditional update, so a redelivered
job finds it spent and completes without repeating the mutation. The queue guarantees the work is
attempted; the layer below guarantees it happens once.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Integer,
    Text,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.platform.db import Base
from app.platform.ids import uuid7

_TS = TIMESTAMP(timezone=True)

#: How long a failed attempt waits before it is eligible again. Linear rather than exponential:
#: these jobs execute an approval a person is waiting on, so a long backoff is a worse failure than
#: a few extra attempts.
RETRY_DELAY = dt.timedelta(seconds=30)


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5", nullable=False)
    run_after: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    finished_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    #: What makes enqueueing idempotent. Two requests to execute the same approval produce one row,
    #: so a double-click cannot become two queued mutations.
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        _TS, server_default=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)


@dataclasses.dataclass(frozen=True, slots=True)
class JobRecord:
    """A claimed job, detached from the session so a handler cannot write through it."""

    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def enqueue(
    session: Session,
    *,
    org_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    max_attempts: int = 5,
    run_after: dt.datetime | None = None,
) -> Job | None:
    """Add a job, or return None if an identical one is already queued.

    The partial unique index does the deduplication, so two concurrent enqueues race in the
    database rather than in Python. Returning None rather than raising because a caller asking for
    work that is already queued got what they wanted.

    **`run_after` is left to the database.** `claim` selects on `run_after <= now()`, evaluated by
    PostgreSQL, so a `run_after` stamped from the enqueuing process's clock would be two clocks
    either side of one comparison. Where those clocks disagree — separate hosts, ordinary NTP error
    — a job enqueued "now" can land in the database's future and stay invisible to the claim until
    they converge. Omitting the column lets its `server_default` of `now()` apply, which makes the
    comparison self-consistent by construction rather than by the two machines agreeing.
    """
    if dedupe_key is not None:
        existing = session.scalars(
            select(Job).where(
                Job.org_id == org_id,
                Job.kind == kind,
                Job.dedupe_key == dedupe_key,
                Job.status.in_(("pending", "running")),
            )
        ).first()
        if existing is not None:
            return None

    job = Job(
        id=uuid7(),
        org_id=org_id,
        kind=kind,
        payload=payload,
        dedupe_key=dedupe_key,
        max_attempts=max_attempts,
    )
    if run_after is not None:
        # A caller that named a time meant that time. Scheduling something for later is a decision,
        # not a reading of the clock, so it is left exactly as given.
        job.run_after = run_after
    session.add(job)
    session.flush()
    return job


def claim(session: Session, *, kinds: tuple[str, ...] | None = None) -> JobRecord | None:
    """Take the next runnable job, or None.

    `FOR UPDATE SKIP LOCKED` means a row another worker is holding is invisible to this one rather
    than a wait. The claim, the work and the completion are all in the caller's transaction, so
    there is no window in which a job is marked running and the work has not happened.

    The session must **not** be organization-scoped when this runs: a worker serves every tenant,
    and the org context is established from the claimed row (see `scope_to`). That is why this is
    the one query in the system that deliberately reads across organizations, and why the worker
    runs as a role that can.
    """
    clause = "AND kind = ANY(:kinds)" if kinds else ""
    row = session.execute(
        text(
            f"""
            SELECT id, org_id, kind, payload, attempts, max_attempts
            FROM job
            WHERE status = 'pending' AND run_after <= now() {clause}
            ORDER BY run_after, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ),
        {"kinds": list(kinds)} if kinds else {},
    ).mappings().first()
    if row is None:
        return None

    session.execute(
        update(Job)
        .where(Job.id == row["id"])
        .values(
            status="running",
            attempts=Job.attempts + 1,
            started_at=dt.datetime.now(dt.UTC),
            version=Job.version + 1,
        )
    )
    return JobRecord(
        id=row["id"],
        org_id=row["org_id"],
        kind=row["kind"],
        payload=dict(row["payload"]),
        attempts=row["attempts"] + 1,
        max_attempts=row["max_attempts"],
    )


def succeed(session: Session, job_id: uuid.UUID) -> None:
    session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status="succeeded",
            finished_at=dt.datetime.now(dt.UTC),
            last_error=None,
            version=Job.version + 1,
        )
    )


def kill(session: Session, record: JobRecord, error: str) -> None:
    """Terminal failure. The job is `dead` and will not be retried.

    Distinct from `fail` because some failures cannot improve by being tried again — an approval
    whose execution window has passed is the case this exists for (ADR-0051). Retrying would burn
    attempts against a state that will never change, and the queue would look busy while nothing
    could ever happen.

    `attempts` is written absolutely, for the same reason as in `fail`: the handler's transaction
    was rolled back and took the claim's increment with it.
    """
    session.execute(
        update(Job)
        .where(Job.id == record.id)
        .values(
            attempts=record.attempts,
            status="dead",
            finished_at=dt.datetime.now(dt.UTC),
            last_error=error[:2000],
            version=Job.version + 1,
        )
    )


def fail(session: Session, record: JobRecord, error: str) -> None:
    """Record a failure, and decide whether it is worth another attempt.

    `attempts` is written as an absolute value rather than incremented here, and that is the whole
    subtlety of this function. The handler runs inside the transaction that claimed the job, so a
    failure has to roll that transaction back before the failure can be recorded — and the rollback
    takes the claim's `attempts = attempts + 1` with it. Incrementing again from whatever is now in
    the row would re-read the pre-claim value, the counter would never move, and a permanently
    failing job would retry forever instead of reaching `dead`.

    `record.attempts` is the count as of the claim, which survives in memory precisely because it
    is not in the transaction that was rolled back.

    A job that has exhausted its attempts becomes `dead` rather than being deleted. Somebody has to
    be able to see what stopped; a queue that tidies away its failures leaves a gap instead of a
    fault.

    The retry time is `now() + RETRY_DELAY` computed by the database, for the same reason `enqueue`
    leaves `run_after` to the server: the delay is only meaningful against the clock that will
    later decide whether it has elapsed.
    """
    exhausted = record.attempts >= record.max_attempts
    session.execute(
        update(Job)
        .where(Job.id == record.id)
        .values(
            attempts=record.attempts,
            status="dead" if exhausted else "pending",
            run_after=func.now() + RETRY_DELAY,
            finished_at=dt.datetime.now(dt.UTC) if exhausted else None,
            # Truncated: a stack trace in a queue row is a log line in the wrong place, and an
            # unbounded one is a way to fill a table with somebody else's error.
            last_error=error[:2000],
            version=Job.version + 1,
        )
    )


def scope_to(session: Session, org_id: uuid.UUID) -> None:
    """Bind the session to the job's organization before the handler runs.

    Transaction-local, so it is discarded at commit and the next claim starts unscoped. A worker
    that leaked the previous job's organization into the next one would be a cross-tenant read with
    no request to blame it on.
    """
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )


__all__ = [
    "RETRY_DELAY",
    "Job",
    "JobRecord",
    "claim",
    "enqueue",
    "fail",
    "kill",
    "scope_to",
    "succeed",
]
