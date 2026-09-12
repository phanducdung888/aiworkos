"""Approval → queue → worker → exactly one mutation (ADR-0044, ADR-0048).

The loop runs as `workos_worker` throughout. That is not a detail: since Checkpoint 9 the queue
exemption is an identity rather than a session state (ADR-0046), so running these as the
application role would prove the loop works against an exemption the application role no longer
has — which is the exact regression this checkpoint exists to prevent.

The queue delivers at-least-once and says so. Exactly-once *execution* comes from the layer below:
`ProposalService.execute` claims the ApprovalRecord with a conditional update, so a redelivered job
finds it spent and completes without repeating the mutation. Neither layer is sufficient alone, and
most of this file is about the composition rather than about either half.

Jobs are run through `run_once` rather than by starting a worker process. The loop is a `while` and
a `sleep`; the behaviour worth testing is what one claim does, and running it directly makes the
duplicate-delivery and failure cases expressible instead of a matter of timing.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform import jobs
from app.workers.runner import RunReport, run_once
from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)


def a_title(label: str) -> str:
    """A title unique to one test.

    `drain()` runs every pending job, including ones an earlier test enqueued deliberately without
    running — `test_queueing_does_not_execute` leaves exactly that. A shared title would then be
    counted twice and the failure would appear in whichever test happened to run second, which is
    the kind of flake that gets rerun rather than read.
    """
    return f"{label}-{uuid.uuid4().hex[:10]}"


def a_proposal(
    api: TestClient,
    headers: dict[str, str],
    routed_to: uuid.UUID,
    *,
    title: str | None = None,
    **over: object,
) -> dict:
    payload: dict[str, object] = {
        "kind": "create",
        "target_type": "work",
        "summary": "Send the revised quote",
        "tool": "create_work",
        "tool_version": "v1",
        "arguments": {"title": title or a_title("queued")},
        "routed_to_person_id": str(routed_to),
    }
    payload.update(over)
    response = api.post("/api/v1/proposals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def approve(api: TestClient, headers: dict[str, str], proposal: dict) -> dict:
    response = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**headers, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert response.status_code == 201, response.text
    return response.json()


def drain(factory: sessionmaker[Session]) -> RunReport:
    """Run every runnable job and total the outcomes.

    `run_once` claims the *oldest* pending job, which in a shared test database is often one another
    test left behind. Draining makes each test's assertion about the queue's final state rather
    than about which row happened to be first — and it is also what a real worker does, so nothing
    here depends on a scheduling detail the production loop does not guarantee either.
    """
    total = RunReport(claimed=0, succeeded=0, failed=0)
    while True:
        report = run_once(factory)
        if report.claimed == 0:
            return total
        total = RunReport(
            claimed=total.claimed + report.claimed,
            succeeded=total.succeeded + report.succeeded,
            failed=total.failed + report.failed,
        )


def work_count(session: Session, title: str) -> int:
    session.rollback()
    return session.execute(
        text("SELECT count(*) FROM work WHERE title = :title"), {"title": title}
    ).scalar_one()


def job_row(session: Session, approval_id: str) -> dict:
    session.rollback()
    return dict(
        session.execute(
            text("SELECT * FROM job WHERE dedupe_key = :key"), {"key": approval_id}
        )
        .mappings()
        .one()
    )


# --------------------------------------------------------------------------- the happy path


def test_queueing_does_not_execute(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """202, and nothing has happened yet. Saying otherwise is a lie the client would build on."""
    title = a_title("not-executed")
    proposal = a_proposal(api, as_admin, work_org.admin, title=title)
    record = approve(api, as_admin, proposal)

    queued = api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    assert queued.status_code == 202
    assert queued.json()["queued"] is True
    assert work_count(scoped_session, title) == 0
    assert job_row(scoped_session, record["id"])["status"] == "pending"


def test_the_worker_executes_a_queued_approval(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session], scoped_session: Session,
) -> None:
    title = a_title("executed-once")
    proposal = a_proposal(api, as_admin, work_org.admin, title=title)
    record = approve(api, as_admin, proposal)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)

    report = drain(worker_session_factory)
    assert report.succeeded >= 1

    assert work_count(scoped_session, title) == 1
    assert job_row(scoped_session, record["id"])["status"] == "succeeded"
    approval = api.get(
        f"/api/v1/proposals/{proposal['id']}/approval", headers=as_admin
    ).json()
    assert approval["execution_status"] == "executed"
    assert approval["resulting_entity_type"] == "work"


def test_an_idle_worker_claims_nothing(
    worker_session_factory: sessionmaker[Session]
) -> None:
    """The control. A worker that reported work when there was none would make every other
    assertion here meaningless."""
    drain(worker_session_factory)
    assert run_once(worker_session_factory).claimed == 0


# --------------------------------------------------------------------------- exactly once


def test_queueing_twice_produces_one_job(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """A double-click must not become two queued mutations."""
    proposal = a_proposal(api, as_admin, work_org.admin)
    record = approve(api, as_admin, proposal)

    first = api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    second = api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    assert first.json()["queued"] is True
    assert second.json()["queued"] is False, "the second enqueue should be a no-op"

    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM job WHERE dedupe_key = :key"), {"key": record["id"]}
    ).scalar_one() == 1


def test_a_duplicate_delivery_executes_once(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    app_session_factory: sessionmaker[Session],
    scoped_session: Session,
) -> None:
    """At-least-once delivery meeting exactly-once execution (ADR-0044).

    The job is re-enqueued *after* it ran, which is what a redelivery looks like: the queue thinks
    the work is outstanding, and the approval knows better.
    """
    title = a_title("redelivered")
    proposal = a_proposal(api, as_admin, work_org.admin, title=title)
    record = approve(api, as_admin, proposal)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    assert drain(worker_session_factory).succeeded >= 1

    session = app_session_factory()
    # Scoped, because `job_enqueue_is_scoped` requires it: a job can only be created from inside a
    # request that already established an organization (ADR-0044).
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(work_org.org_id)},
    )
    jobs.enqueue(
        session,
        org_id=work_org.org_id,
        kind="execute_approval",
        payload={"approval_id": record["id"]},
        dedupe_key=None,
    )
    session.commit()
    session.close()

    second = drain(worker_session_factory)
    assert second.claimed == 1
    assert second.succeeded == 1, "a redelivery is not a failure; it is already-done"
    assert work_count(scoped_session, title) == 1


def test_two_workers_do_not_both_claim_the_same_job(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session], scoped_session: Session,
) -> None:
    """`SKIP LOCKED` in the claim, tested by holding the row and claiming again.

    The second session must see nothing rather than wait, which is what lets several workers run
    without a lock service.
    """
    proposal = a_proposal(api, as_admin, work_org.admin)
    record = approve(api, as_admin, proposal)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)

    holder = worker_session_factory()
    claimed = jobs.claim(holder, kinds=("execute_approval",))
    assert claimed is not None, "the first worker should claim something"
    try:
        other = worker_session_factory()
        try:
            second = jobs.claim(other, kinds=("execute_approval",))
            # Not "claimed nothing": the suite leaves other pending jobs behind, so a second
            # worker finding *different* work is correct and is what SKIP LOCKED is for. What must
            # never happen is two workers holding the same row.
            assert second is None or second.id != claimed.id, (
                "a second worker claimed the job another is already holding"
            )
        finally:
            other.rollback()
            other.close()
    finally:
        holder.rollback()
        holder.close()


# --------------------------------------------------------------------------- failure


def test_a_failing_job_is_retried_and_leaves_no_partial_state(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session], scoped_session: Session,
) -> None:
    """The action names a Project that does not exist, so the Work Core refuses it.

    Two things must hold afterwards: no Work, and an approval still spendable. Asserting only the
    first would pass against an implementation that also lost the approval.
    """
    doomed = a_title("doomed")
    proposal = a_proposal(
        api,
        as_admin,
        work_org.admin,
        arguments={"title": doomed, "project_id": str(uuid.uuid4())},
    )
    record = approve(api, as_admin, proposal)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)

    report = drain(worker_session_factory)
    assert report.failed >= 1

    assert work_count(scoped_session, doomed) == 0
    row = job_row(scoped_session, record["id"])
    assert row["status"] == "pending", "a failed job should be retryable"
    assert row["attempts"] == 1
    assert row["last_error"]

    approval = api.get(
        f"/api/v1/proposals/{proposal['id']}/approval", headers=as_admin
    ).json()
    assert approval["execution_status"] == "pending", (
        "a failed execution must leave the approval usable; a claim that survives a rollback "
        "would strand it forever"
    )


def test_a_job_that_exhausts_its_attempts_is_kept_as_dead(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    app_session_factory: sessionmaker[Session],
    scoped_session: Session,
) -> None:
    """A queue that tidies away its failures leaves a gap instead of a fault."""
    proposal = a_proposal(
        api,
        as_admin,
        work_org.admin,
        arguments={"title": a_title("doomed-twice"), "project_id": str(uuid.uuid4())},
    )
    record = approve(api, as_admin, proposal)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)

    session = app_session_factory()
    # Scoped, because since Checkpoint 9 the application role has no queue exemption (ADR-0046).
    # Without this the UPDATE matches no rows and says so by changing nothing, which is RLS
    # working and is exactly the regression this test would otherwise hide.
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(work_org.org_id)},
    )
    session.execute(
        text(
            "UPDATE job SET max_attempts = 1, run_after = now() - interval '1 minute' "
            "WHERE dedupe_key = :key"
        ),
        {"key": record["id"]},
    )
    session.commit()
    session.close()

    assert drain(worker_session_factory).failed >= 1
    row = job_row(scoped_session, record["id"])
    assert row["status"] == "dead"
    assert row["finished_at"] is not None
    assert row["last_error"]


def test_a_rejected_approval_completes_the_job_without_executing(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    app_session_factory: sessionmaker[Session],
    scoped_session: Session,
) -> None:
    """Retrying forever against a state that will never change is a stuck queue, not a safeguard."""
    never = a_title("never")
    proposal = a_proposal(api, as_admin, work_org.admin, arguments={"title": never})
    rejected = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "rejected"},
        headers={**as_admin, "If-Match": f'W/"{proposal["version"]}"'},
    ).json()

    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(work_org.org_id)},
    )
    jobs.enqueue(
        session,
        org_id=work_org.org_id,
        kind="execute_approval",
        payload={"approval_id": rejected["id"]},
        dedupe_key=rejected["id"],
    )
    session.commit()
    session.close()

    report = drain(worker_session_factory)
    assert report.succeeded >= 1, "the job is complete; there is nothing to retry"
    assert work_count(scoped_session, never) == 0


# --------------------------------------------------------------------------- tenancy


def test_the_worker_scopes_itself_to_the_job_s_organization(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session], scoped_session: Session,
) -> None:
    """A worker serves every tenant and must not leak one into the next.

    The mutation it produces has to land in the job's organization — which is the only reason the
    claim query is allowed to read across organizations at all.
    """
    scoped = a_title("scoped")
    proposal = a_proposal(api, as_admin, work_org.admin, arguments={"title": scoped})
    record = approve(api, as_admin, proposal)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    assert drain(worker_session_factory).succeeded >= 1

    scoped_session.rollback()
    org_id = scoped_session.execute(
        text("SELECT org_id FROM work WHERE title = :title"), {"title": scoped}
    ).scalar_one()
    assert org_id == work_org.org_id
