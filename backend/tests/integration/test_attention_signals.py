"""The facts the management view is built out of (CP17).

The Attention screen invents nothing. Every section on it is one of these queries, and every one is
a rule somebody can state in a sentence: past its date, waiting for a decision, stopped with a
recorded cause. This file is where those queries are held to that claim — because a dashboard built
on assumptions about a filter is a dashboard that is confidently wrong.

The load-bearing assertion is the last one: **asking these questions writes nothing.** An attention
view that marked promises missed by being looked at would make a status change appear with no actor
behind it (BR-C-06).
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration

TODAY = dt.date(2026, 9, 12)
LONG_PAST = dt.date(2020, 1, 1)


def a_work(api: TestClient, headers: dict[str, str], **over: object) -> dict:
    body: dict[str, object] = {"title": f"Work {uuid.uuid4().hex[:8]}"}
    body.update(over)
    response = api.post(
        "/api/v1/work", json=body, headers={**headers, "Idempotency-Key": str(uuid.uuid4())}
    )
    assert response.status_code == 201, response.text
    return response.json()


def a_commitment(
    api: TestClient, headers: dict[str, str], person: uuid.UUID, **over: object
) -> dict:
    body: dict[str, object] = {
        "statement": f"Promise {uuid.uuid4().hex[:8]}",
        "committed_by_person_id": str(person),
        "due_precision": "vague",
    }
    body.update(over)
    response = api.post(
        "/api/v1/commitments",
        json=body,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def move(api: TestClient, headers: dict[str, str], commitment: dict, target: str) -> dict:
    response = api.post(
        f"/api/v1/commitments/{commitment['id']}/status",
        json={"target": target},
        headers={**headers, "If-Match": f'W/"{commitment["version"]}"'},
    )
    assert response.status_code == 200, response.text
    return response.json()


def ids(payload: dict) -> set[str]:
    return {row["id"] for row in payload["items"]}


# --------------------------------------------------------------------------- commitments


def test_promises_nobody_confirmed_are_findable(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """`captured` is what the system heard; `open` is what somebody agreed to (BR-C-05).

    The distinction is the whole reason this is its own section: an unconfirmed promise needs a
    person to look at it, and a late one needs somebody to do something about it.
    """
    heard = a_commitment(api, as_admin, work_org.member)
    agreed = move(api, as_admin, a_commitment(api, as_admin, work_org.admin), "open")

    captured = api.get("/api/v1/commitments", params={"status": "captured"}, headers=as_admin)
    assert heard["id"] in ids(captured.json())
    assert agreed["id"] not in ids(captured.json())


def test_promises_due_soon_are_findable_and_exclude_the_undated(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """ADR-0055's payoff. A promise with no date cannot appear in a deadline list at all."""
    soon = move(
        api,
        as_admin,
        a_commitment(
            api, as_admin, work_org.admin, due_date=TODAY.isoformat(), due_precision="exact"
        ),
        "open",
    )
    undated = move(api, as_admin, a_commitment(api, as_admin, work_org.admin), "open")

    listed = api.get(
        "/api/v1/commitments",
        params={"status": "open", "due_before": (TODAY + dt.timedelta(days=7)).isoformat()},
        headers=as_admin,
    ).json()
    assert soon["id"] in ids(listed)
    assert undated["id"] not in ids(listed), "a promise with no date appeared in a deadline list"


def test_overdue_is_computed_and_skips_vague_promises(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-C-06. Late and dated is overdue; late and vague is a review prompt, not an alert."""
    dated = move(
        api,
        as_admin,
        a_commitment(
            api, as_admin, work_org.admin, due_date=LONG_PAST.isoformat(), due_precision="exact"
        ),
        "open",
    )
    vague = move(api, as_admin, a_commitment(api, as_admin, work_org.admin), "open")
    scoped_session.rollback()
    scoped_session.execute(
        text("UPDATE commitment SET due_date = :past WHERE id = :id"),
        {"past": LONG_PAST, "id": uuid.UUID(vague["id"])},
    )
    scoped_session.commit()

    overdue = api.get("/api/v1/commitments/overdue/today", headers=as_admin).json()
    assert dated["id"] in ids(overdue)
    assert vague["id"] not in ids(overdue), "a deadline nobody set was reported as missed"


# --------------------------------------------------------------------------- work


def test_blocked_work_is_findable_and_carries_its_cause(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-W-04: blocked work always has a recorded reason, so the view never shows a bare status."""
    item = a_work(api, as_admin)
    started = api.post(
        f"/api/v1/work/{item['id']}/status",
        json={"target": "in_progress"},
        headers={**as_admin, "If-Match": f'W/"{item["version"]}"'},
    ).json()
    blocked = api.post(
        f"/api/v1/work/{item['id']}/status",
        json={"target": "blocked", "blocked_reason": "waiting on finance"},
        headers={**as_admin, "If-Match": f'W/"{started["version"]}"'},
    )
    assert blocked.status_code == 200, blocked.text

    listed = api.get("/api/v1/work", params={"status": "blocked"}, headers=as_admin).json()
    row = next(candidate for candidate in listed["items"] if candidate["id"] == item["id"])
    assert row["blocked_reason"] == "waiting on finance"


def test_work_past_its_date_is_findable_including_the_finished(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The filter this view narrows in the client, asserted so the narrowing is deliberate.

    `due_before` does not also exclude settled work, so a `done` item with a past date comes back
    here. The Attention screen drops those; recording the API's actual behaviour means the day
    somebody adds a server-side filter, this test is what tells them the client can stop.
    """
    overdue = a_work(api, as_admin, due_date=LONG_PAST.isoformat())
    done = a_work(api, as_admin, due_date=LONG_PAST.isoformat())
    in_progress = api.post(
        f"/api/v1/work/{done['id']}/status",
        json={"target": "in_progress"},
        headers={**as_admin, "If-Match": f'W/"{done["version"]}"'},
    ).json()
    api.post(
        f"/api/v1/work/{done['id']}/status",
        json={"target": "done"},
        headers={**as_admin, "If-Match": f'W/"{in_progress["version"]}"'},
    )

    listed = api.get(
        "/api/v1/work", params={"due_before": TODAY.isoformat(), "limit": 200}, headers=as_admin
    ).json()
    assert {overdue["id"], done["id"]} <= ids(listed)


# --------------------------------------------------------------------------- the whole screen


def test_the_attention_questions_are_read_only(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Every query the view makes, run against a populated organization, changing nothing.

    The snapshot compares the rows that would record a change — statuses and versions — before and
    after. A read that transitioned anything would show up here as a different version, which is
    the only evidence worth taking for "this page is safe to look at".
    """
    late = move(
        api,
        as_admin,
        a_commitment(
            api, as_admin, work_org.admin, due_date=LONG_PAST.isoformat(), due_precision="exact"
        ),
        "open",
    )
    a_commitment(api, as_admin, work_org.member)
    a_work(api, as_admin, due_date=LONG_PAST.isoformat())

    def snapshot() -> list[tuple]:
        scoped_session.rollback()
        return [
            tuple(row)
            for row in scoped_session.execute(
                text(
                    "SELECT id, status, version FROM commitment WHERE org_id = :org "
                    "UNION ALL SELECT id, status, version FROM work WHERE org_id = :org "
                    "ORDER BY 1"
                ),
                {"org": work_org.org_id},
            )
        ]

    before = snapshot()
    for path, params in (
        ("/api/v1/commitments/overdue/today", {}),
        ("/api/v1/commitments", {"status": "captured"}),
        ("/api/v1/commitments", {"status": "open", "due_before": TODAY.isoformat()}),
        ("/api/v1/proposals", {"status": "pending"}),
        ("/api/v1/work", {"status": "blocked"}),
        ("/api/v1/work", {"due_before": TODAY.isoformat()}),
    ):
        assert api.get(path, params=params, headers=as_admin).status_code == 200

    assert snapshot() == before
    # And the late promise is still `open`, not `missed`: overdue is a question (BR-C-06).
    assert api.get(f"/api/v1/commitments/{late['id']}", headers=as_admin).json()["status"] == "open"
