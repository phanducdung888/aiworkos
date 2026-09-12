"""The approval execution window (BR-AI-22, ADR-0051).

An ApprovalRecord authorises one mutation for 24 hours. After that it authorises nothing and the
action must be approved again.

The deadline is **derived**, not stored: `decided_at + EXECUTION_WINDOW`, where `decided_at` is
frozen by the immutability trigger. So there is no column to drift, no backfill, and the same
approval has the same deadline in every process on every attempt. These tests move `decided_at`
directly — the one way to make an approval old without waiting a day — which is also a nice
demonstration that the deadline follows the immutable field rather than a mutable copy of it.

The enforcement point is the claim. A worker that reads an unexpired approval and is descheduled
past the deadline still fails, because the deadline is part of the `UPDATE` that claims it. Every
test below is a different route to that same statement.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.intelligence.public import EXECUTION_WINDOW, execution_deadline
from app.workers.runner import run_once
from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration


def a_title(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:10]}"


def approved(
    api: TestClient, headers: dict[str, str], routed_to: uuid.UUID, title: str
) -> dict:
    proposal = api.post(
        "/api/v1/proposals",
        json={
            "kind": "create",
            "target_type": "work",
            "summary": "Send the revised quote",
            "tool": "create_work",
            "tool_version": "v1",
            "arguments": {"title": title},
            "routed_to_person_id": str(routed_to),
        },
        headers=headers,
    )
    assert proposal.status_code == 201, proposal.text
    body = proposal.json()
    record = api.post(
        f"/api/v1/proposals/{body['id']}/decision",
        json={"decision": "approved"},
        headers={**headers, "If-Match": f'W/"{body["version"]}"'},
    )
    assert record.status_code == 201, record.text
    return record.json()


def age_approval(
    owner_session: Session, org_id: uuid.UUID, approval_id: str, *, by: dt.timedelta
) -> None:
    """Move `decided_at` back, which is the only honest way to make an approval old.

    Run as the table owner with the immutability trigger briefly disabled, because `decided_at` is
    frozen against everything including a test. That is the point being demonstrated: the deadline
    follows an immutable field, so making an approval expire requires reaching past a control that
    no application path can. If the deadline were a stored column, this helper would edit that
    column instead and the test would stop proving anything about derivation.
    """
    owner_session.rollback()
    owner_session.execute(
        text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(org_id)}
    )
    owner_session.execute(
        text("ALTER TABLE approval_record DISABLE TRIGGER trg_approval_record_immutable")
    )
    owner_session.execute(
        text("UPDATE approval_record SET decided_at = decided_at - :age WHERE id = :id"),
        {"age": by, "id": uuid.UUID(approval_id)},
    )
    owner_session.execute(
        text("ALTER TABLE approval_record ENABLE TRIGGER trg_approval_record_immutable")
    )
    owner_session.commit()
    owner_session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
    owner_session.commit()


def drain(factory: sessionmaker[Session]) -> None:
    while run_once(factory).claimed:
        pass


def work_count(session: Session, title: str) -> int:
    session.rollback()
    return session.execute(
        text("SELECT count(*) FROM work WHERE title = :title"), {"title": title}
    ).scalar_one()


def job_status(session: Session, approval_id: str) -> dict:
    session.rollback()
    return dict(
        session.execute(
            text("SELECT status, attempts, last_error FROM job WHERE dedupe_key = :key"),
            {"key": approval_id},
        )
        .mappings()
        .one()
    )


# --------------------------------------------------------------------------- the deadline


def test_the_deadline_is_derived_from_the_approval(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """Published as a computed field, so a client can show it without a second source of truth."""
    record = approved(api, as_admin, work_org.admin, a_title("derived"))
    read = api.get(f"/api/v1/approvals/{record['id']}", headers=as_admin).json()

    decided = dt.datetime.fromisoformat(read["decided_at"])
    expires = dt.datetime.fromisoformat(read["execution_expires_at"])
    assert expires == decided + EXECUTION_WINDOW
    assert dt.timedelta(hours=24) == EXECUTION_WINDOW


def test_the_deadline_does_not_move_between_reads(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The property a stored column would not give.

    A deadline that shifted between a worker's attempts would make "was this expired" depend on
    when you asked rather than on which approval it is.
    """
    record = approved(api, as_admin, work_org.admin, a_title("stable"))
    reads = [
        api.get(f"/api/v1/approvals/{record['id']}", headers=as_admin).json()[
            "execution_expires_at"
        ]
        for _ in range(3)
    ]
    assert len(set(reads)) == 1


# --------------------------------------------------------------------------- before the deadline


def test_a_fresh_approval_executes(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    """The control. Without it, an implementation that refused everything would pass this file."""
    title = a_title("in-time")
    record = approved(api, as_admin, work_org.admin, title)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    drain(worker_session_factory)

    assert work_count(scoped_session, title) == 1
    assert job_status(scoped_session, record["id"])["status"] == "succeeded"


# --------------------------------------------------------------------------- after the deadline


def test_an_expired_approval_does_not_execute(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    title = a_title("expired")
    record = approved(api, as_admin, work_org.admin, title)
    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=1),
    )

    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    drain(worker_session_factory)

    assert work_count(scoped_session, title) == 0
    assert job_status(scoped_session, record["id"])["status"] == "dead"


def test_queued_before_the_deadline_and_run_after_it_is_blocked(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    """The gap the whole design is about.

    Checking expiry only at enqueue would pass this and be wrong: the deadline passes precisely in
    the window between queueing and running, which is the window a queue exists to create.
    """
    title = a_title("queued-then-expired")
    record = approved(api, as_admin, work_org.admin, title)
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)

    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=1),
    )
    drain(worker_session_factory)

    assert work_count(scoped_session, title) == 0
    assert job_status(scoped_session, record["id"])["status"] == "dead"


def test_a_retry_after_the_deadline_is_blocked(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    """A job that failed while in time must not succeed by being retried out of time."""
    title = a_title("retry-expired")
    proposal = api.post(
        "/api/v1/proposals",
        json={
            "kind": "create",
            "target_type": "work",
            "summary": "Doomed",
            "tool": "create_work",
            "tool_version": "v1",
            # A project that does not exist, so the first attempt fails and the job goes back to
            # pending with its attempt recorded.
            "arguments": {"title": title, "project_id": str(uuid.uuid4())},
            "routed_to_person_id": str(work_org.admin),
        },
        headers=as_admin,
    ).json()
    record = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**as_admin, "If-Match": f'W/"{proposal["version"]}"'},
    ).json()

    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    drain(worker_session_factory)
    first = job_status(scoped_session, record["id"])
    assert first["status"] == "pending" and first["attempts"] == 1

    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=1),
    )
    scoped_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(work_org.org_id)},
    )
    scoped_session.execute(
        text("UPDATE job SET run_after = now() - interval '1 minute' WHERE dedupe_key = :key"),
        {"key": record["id"]},
    )
    scoped_session.commit()
    drain(worker_session_factory)

    assert work_count(scoped_session, title) == 0
    after = job_status(scoped_session, record["id"])
    assert after["status"] == "dead", "a retry must not outlive the approval it runs under"
    assert "BR-AI-22" in (after["last_error"] or "")


def test_a_duplicate_delivery_after_the_deadline_is_blocked(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session,
    app_session_factory: sessionmaker[Session],
    scoped_session: Session,
) -> None:
    """At-least-once delivery meeting a closed window.

    The job is re-enqueued after the deadline, which is what a redelivery looks like: the queue
    thinks the work is outstanding and the approval knows better.
    """
    from app.platform import jobs

    title = a_title("duplicate-expired")
    record = approved(api, as_admin, work_org.admin, title)
    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=1),
    )

    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(work_org.org_id)},
    )
    jobs.enqueue(
        session,
        org_id=work_org.org_id,
        kind="execute_approval",
        payload={"approval_id": record["id"]},
        dedupe_key=record["id"],
    )
    session.commit()
    session.close()

    drain(worker_session_factory)
    assert work_count(scoped_session, title) == 0


def test_the_approval_is_never_marked_executed_after_expiry(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    """No Tool Gateway call, and no ToolCall recorded as a successful execution."""
    title = a_title("never-executed")
    record = approved(api, as_admin, work_org.admin, title)
    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(hours=2),
    )
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    drain(worker_session_factory)

    read = api.get(f"/api/v1/approvals/{record['id']}", headers=as_admin).json()
    assert read["execution_status"] == "pending"
    assert read["resulting_entity_id"] is None
    assert read["executed_at"] is None


def test_the_expired_attempt_leaves_audit_evidence(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    """A refusal nobody can see is a refusal nobody can investigate.

    The job row carries the reason and names the rule, which is what somebody looking at a stuck
    approval needs in order to know it was refused rather than forgotten.
    """
    title = a_title("audited-expiry")
    record = approved(api, as_admin, work_org.admin, title)
    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=5),
    )
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    drain(worker_session_factory)

    row = job_status(scoped_session, record["id"])
    assert row["status"] == "dead"
    assert "BR-AI-22" in (row["last_error"] or "")
    assert "approved again" in (row["last_error"] or "")


def test_expiry_is_terminal_rather_than_retried(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    """An approval cannot become unexpired.

    Retrying would burn five attempts against a state that will never change, and the queue would
    look busy while nothing could ever happen.
    """
    record = approved(api, as_admin, work_org.admin, a_title("terminal"))
    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=1),
    )
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    drain(worker_session_factory)

    row = job_status(scoped_session, record["id"])
    assert row["status"] == "dead"
    assert row["attempts"] == 1, "expiry must not consume the retry budget"


def test_the_approval_record_stays_immutable(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
    owner_session: Session, scoped_session: Session,
) -> None:
    """Expiry changes nothing about the approval — it is the job that dies.

    The decision is an immutable record of what a human authorised; it does not become false
    because time passed, it stops being actionable.
    """
    record = approved(api, as_admin, work_org.admin, a_title("immutable"))
    before = api.get(f"/api/v1/approvals/{record['id']}", headers=as_admin).json()
    age_approval(
        owner_session,
        work_org.org_id,
        record["id"],
        by=EXECUTION_WINDOW + dt.timedelta(minutes=1),
    )
    api.post(f"/api/v1/approvals/{record['id']}/queue", headers=as_admin)
    drain(worker_session_factory)

    after = api.get(f"/api/v1/approvals/{record['id']}", headers=as_admin).json()
    assert after["approved_action_hash"] == before["approved_action_hash"]
    assert after["decision"] == before["decision"]
    assert after["execution_status"] == "pending"


def test_the_deadline_follows_the_approval_not_the_clock_at_claim_time(
    scoped_session: Session,
) -> None:
    """`execution_deadline` is a pure function of an immutable field and one constant."""
    decided = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
    assert execution_deadline(decided) == decided + EXECUTION_WINDOW
    assert execution_deadline(decided) == execution_deadline(decided)
