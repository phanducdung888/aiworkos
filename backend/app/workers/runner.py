"""The job worker (ADR-0044).

One loop, one claim at a time, one transaction per job. The transaction is the whole safety story:
claiming the row, running the handler and marking the job finished all commit together, so a crash
at any point leaves the job unclaimed and retryable rather than lost or half-done.

Deliberately not a framework. There is no scheduler, no broker and no process manager here — a
worker is a process that runs this loop, and running several is safe because `SKIP LOCKED` means
they never contend for the same row.

The session is **not** organization-scoped when a job is claimed: a worker serves every tenant, and
the org context is set from the claimed row before the handler runs. That is the one place in the
system where a query deliberately crosses organizations, and it reads nothing but the queue.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

import app.contexts.intelligence.public as intelligence
from app.platform import jobs

logger = logging.getLogger(__name__)

Handler = Callable[[Session, uuid.UUID, dict[str, Any]], str]

#: How long to sleep when the queue is empty. Long enough that an idle worker costs one cheap
#: indexed query every few seconds; short enough that an approved action does not sit visibly
#: waiting. `LISTEN/NOTIFY` would remove the idle query and is deliberately not used yet — it adds
#: a second delivery path that has to be correct *as well as* this one, not instead of it.
IDLE_SLEEP_SECONDS = 2.0


def _execute_approval(session: Session, org_id: uuid.UUID, payload: dict[str, Any]) -> str:
    return intelligence.execute_queued_approval(session, org_id=org_id, payload=payload)


#: Every kind of work this system queues. A closed map, for the same reason the tool registry is
#: closed (ADR-0042): a queue that dispatches on an arbitrary string is a way to run arbitrary code
#: by writing a row.
HANDLERS: dict[str, Handler] = {
    intelligence.EXECUTE_APPROVAL: _execute_approval,
}


@dataclasses.dataclass(frozen=True, slots=True)
class RunReport:
    claimed: int
    succeeded: int
    failed: int


def run_once(factory: sessionmaker[Session]) -> RunReport:
    """Claim and run at most one job. Returns what happened, for the loop and for tests.

    A fresh session per job, so a failure cannot leave state visible to the next one.
    """
    session = factory()
    try:
        record = jobs.claim(session, kinds=tuple(HANDLERS))
        if record is None:
            session.rollback()
            return RunReport(claimed=0, succeeded=0, failed=0)

        handler = HANDLERS.get(record.kind)
        if handler is None:  # pragma: no cover - claim() filters by kind
            jobs.fail(session, record, f"no handler for {record.kind}")
            session.commit()
            return RunReport(claimed=1, succeeded=0, failed=1)

        # Scope before the handler runs, so everything it touches is subject to RLS for the job's
        # organization — including the queries it makes through application services.
        jobs.scope_to(session, record.org_id)
        try:
            outcome = handler(session, record.org_id, record.payload)
        except Exception as error:
            # Roll the mutation back *first*, then record the failure in a clean transaction. A
            # failure written inside the poisoned transaction would be rolled back with it, and the
            # job would look untouched.
            session.rollback()
            jobs.fail(session, record, f"{type(error).__name__}: {error}")
            session.commit()
            logger.warning(
                "job %s (%s) failed on attempt %s: %s",
                record.id,
                record.kind,
                record.attempts,
                error,
            )
            return RunReport(claimed=1, succeeded=0, failed=1)

        jobs.succeed(session, record.id)
        session.commit()
        logger.info("job %s (%s) succeeded: %s", record.id, record.kind, outcome)
        return RunReport(claimed=1, succeeded=1, failed=0)
    finally:
        session.close()


def run_forever(
    factory: sessionmaker[Session], *, idle_sleep: float = IDLE_SLEEP_SECONDS
) -> None:  # pragma: no cover - the loop itself is not what needs testing
    """Run until interrupted. `run_once` is where the behaviour is, and where the tests are."""
    logger.info("worker started; handlers: %s", sorted(HANDLERS))
    while True:
        try:
            report = run_once(factory)
        except Exception:
            # A failure to *claim* is infrastructure — the database is unreachable or the query is
            # wrong. Neither is fixed by exiting, and exiting would make a transient outage look
            # like a deployment problem.
            logger.exception("worker loop error; continuing")
            time.sleep(idle_sleep)
            continue
        if report.claimed == 0:
            time.sleep(idle_sleep)


def main() -> None:  # pragma: no cover - process entry point
    from app.platform.db import worker_session_factory

    logging.basicConfig(level=logging.INFO)
    run_forever(worker_session_factory())


if __name__ == "__main__":  # pragma: no cover
    main()
