"""One clock decides when a job runs (CP23).

`claim` selects on `run_after <= now()`, evaluated by PostgreSQL. If `run_after` were stamped from
the enqueuing process's clock, that single comparison would straddle two clocks — and where they
disagree, which on separate hosts with ordinary NTP error they do, a job enqueued "now" lands in the
database's future and stays invisible to the claim until they converge.

These tests make the process clock **wrong on purpose** and assert the queue does not care. A test
that merely enqueued and claimed would pass on a machine whose clocks happen to agree, which is
every machine until the one where it matters.

This is a correctness fix and is **not** a fix for the intermittent execution failure recorded in
`progress.md`. That remains open, and the measurements taken while investigating it put the local
skew firmly in the harmless direction.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform import jobs
from app.platform.ids import uuid7

pytestmark = pytest.mark.integration

#: Far enough that a job scheduled by it would not run today.
SKEW = dt.timedelta(hours=9)


@pytest.fixture
def a_tenant(scoped_session: Session) -> Iterator[uuid.UUID]:
    """An organization of this test's own, so nothing it queues is another test's leftover."""
    org_id = uuid7()
    scoped_session.rollback()
    scoped_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )
    scoped_session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Clock', :slug)"),
        {"id": org_id, "slug": f"clock-{uuid.uuid4().hex[:12]}"},
    )
    scoped_session.commit()
    yield org_id


@pytest.fixture
def clock_runs_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this process believe it is nine hours later than the database does.

    Patched on the module under test rather than globally: the point is what `jobs` does with the
    clock, and moving time for the whole interpreter would move it for the database driver too.
    """

    real = dt.datetime

    class Ahead(real):  # type: ignore[valid-type, misc]
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:  # type: ignore[override]
            # `real`, captured before the patch. Calling `dt.datetime.now` here would resolve to
            # this method again, because the name being patched is the one on the shared module.
            return real.now(tz) + SKEW

    monkeypatch.setattr(jobs.dt, "datetime", Ahead)


def queue_one(session: Session, org_id: uuid.UUID, **over: object) -> jobs.Job:
    # Re-established per transaction: the context is set `is_local`, so the commit that created the
    # organization discarded it, and RLS refuses a row with no organization to claim. That is the
    # control working exactly as intended.
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )
    job = jobs.enqueue(
        session,
        org_id=org_id,
        kind="execute_approval",
        payload={"approval_id": str(uuid7())},
        dedupe_key=str(uuid7()),
        **over,  # type: ignore[arg-type]
    )
    assert job is not None
    session.commit()
    return job


def scheduled_for(
    session: Session, org_id: uuid.UUID, job_id: uuid.UUID
) -> tuple[dt.datetime, dt.datetime]:
    """This job's `run_after`, and the database's own idea of the time."""
    session.rollback()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )
    row = session.execute(
        text("SELECT run_after, now() AS clock FROM job WHERE id = :id"), {"id": job_id}
    ).one()
    return row[0], row[1]


# --------------------------------------------------------------------------- enqueue


def test_a_job_is_runnable_now_even_when_the_process_clock_is_hours_ahead(
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
    a_tenant: uuid.UUID,
    clock_runs_fast: None,
) -> None:
    """The regression. Before this change the job landed nine hours in the database's future."""
    job = queue_one(scoped_session, a_tenant)

    run_after, clock = scheduled_for(scoped_session, a_tenant, job.id)
    assert run_after <= clock, (
        f"the job is scheduled {(run_after - clock)} into the database's future"
    )

    worker = worker_session_factory()
    try:
        claimed = jobs.claim(worker, kinds=("execute_approval",))
        assert claimed is not None, "a job queued now was not claimable"
        assert claimed.id == job.id
        worker.commit()
    finally:
        worker.close()


def test_the_time_is_the_database_s_and_not_the_caller_s(
    scoped_session: Session, a_tenant: uuid.UUID, clock_runs_fast: None
) -> None:
    """Stated as a bound rather than an equality: the two differ by a round trip, not by hours."""
    job = queue_one(scoped_session, a_tenant)
    run_after, clock = scheduled_for(scoped_session, a_tenant, job.id)
    assert abs((clock - run_after).total_seconds()) < 60
    assert (clock - run_after) < SKEW / 2


def test_a_caller_that_names_a_time_still_gets_that_time(
    scoped_session: Session, a_tenant: uuid.UUID
) -> None:
    """Scheduling for later is a decision, not a reading of the clock, and is left alone."""
    later = dt.datetime(2027, 1, 1, 12, 0, tzinfo=dt.UTC)
    job = queue_one(scoped_session, a_tenant, run_after=later)

    run_after, _clock = scheduled_for(scoped_session, a_tenant, job.id)
    assert run_after == later


def test_a_job_scheduled_for_later_is_not_claimable_yet(
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
    a_tenant: uuid.UUID,
) -> None:
    """The other half: the queue still honours a future `run_after`, which is its whole purpose."""
    queue_one(
        scoped_session, a_tenant, run_after=dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    )
    worker = worker_session_factory()
    try:
        # Other tests share this queue, so the assertion is about *this* organization's job.
        for _ in range(50):
            claimed = jobs.claim(worker, kinds=("execute_approval",))
            if claimed is None:
                break
            assert claimed.org_id != a_tenant, "a job scheduled for tomorrow was claimed today"
            jobs.fail(worker, claimed, "drained by test_job_clock")
            worker.commit()
    finally:
        worker.close()


# --------------------------------------------------------------------------- retry


def test_a_retry_is_delayed_by_the_database_clock(
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
    a_tenant: uuid.UUID,
    clock_runs_fast: None,
) -> None:
    """`fail` reschedules with `now() + RETRY_DELAY`, and `now()` has to be the same `now()`.

    A retry stamped nine hours ahead would not be a retry; it would be a job that silently stopped.
    """
    job = queue_one(scoped_session, a_tenant)

    worker = worker_session_factory()
    try:
        claimed = jobs.claim(worker, kinds=("execute_approval",))
        assert claimed is not None and claimed.id == job.id
        jobs.fail(worker, claimed, "a failure worth retrying")
        worker.commit()
    finally:
        worker.close()

    run_after, clock = scheduled_for(scoped_session, a_tenant, job.id)
    delay = (run_after - clock).total_seconds()
    assert 0 < delay <= jobs.RETRY_DELAY.total_seconds() + 5, (
        f"the retry is {delay}s away; RETRY_DELAY is {jobs.RETRY_DELAY.total_seconds()}s"
    )
