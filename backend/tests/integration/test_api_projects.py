"""Project, Milestone, Dependency and ownership over HTTP.

The same four questions the Work slice answered in 4a, asked of the aggregates that were replicated
from it: does the mutation take the full path, is the read filtered rather than trimmed, does the
concurrency story hold, and does another organization's row look like nothing at all.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import Realm, WorkOrg, auth, subject_of

pytestmark = pytest.mark.integration


def a_project(api: TestClient, headers: dict[str, str], org: WorkOrg, **over: object) -> dict:
    payload: dict[str, object] = {"name": "Migration", "owning_team_id": str(org.team_id)}
    payload.update(over)
    response = api.post("/api/v1/projects", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- project


def test_creating_a_project_takes_the_whole_path(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    project = a_project(api, as_admin, work_org, objective="Move off the old stack")
    assert project["status"] == "proposed"
    assert project["created_by_person_id"] == str(work_org.admin)

    scoped_session.rollback()
    audit = scoped_session.execute(
        text("SELECT action, authorization_context FROM audit_entry WHERE resource_id = :id"),
        {"id": uuid.UUID(project["id"])},
    ).mappings().all()
    assert [row["action"] for row in audit] == ["create"]
    assert audit[0]["authorization_context"]["role"] == "org_admin"

    events = scoped_session.execute(
        text("SELECT type FROM outbox WHERE aggregate_id = :id"),
        {"id": uuid.UUID(project["id"])},
    ).scalars().all()
    assert events == ["ProjectCreated"]


def test_a_project_without_a_team_or_department_is_refused_by_the_domain(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """BR-P-01, reaching the wire as a rule id rather than a constraint violation."""
    response = api.post("/api/v1/projects", json={"name": "Homeless"}, headers=as_admin)
    assert response.status_code == 422
    assert response.json()["rule"] == "BR-P-01"


def test_a_member_cannot_create_a_project(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    response = api.post(
        "/api/v1/projects",
        json={"name": "Mine", "owning_team_id": str(work_org.team_id)},
        headers=as_member,
    )
    assert response.status_code == 403


def test_project_status_moves_and_respects_if_match(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    project = a_project(api, as_admin, work_org)
    path = f"/api/v1/projects/{project['id']}/status"

    assert api.post(path, json={"target": "active"}, headers=as_admin).status_code == 428

    activated = api.post(
        path, json={"target": "active"}, headers={**as_admin, "If-Match": 'W/"1"'}
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    stale = api.post(path, json={"target": "on_hold"}, headers={**as_admin, "If-Match": 'W/"1"'})
    assert stale.status_code == 412


def test_an_undeclared_project_transition_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    project = a_project(api, as_admin, work_org)
    response = api.post(
        f"/api/v1/projects/{project['id']}/status",
        json={"target": "completed"},
        headers={**as_admin, "If-Match": 'W/"1"'},
    )
    assert response.status_code == 422
    assert response.json()["rule"] == "BR-P-03"


def test_a_restricted_project_is_invisible_to_a_reader_outside_its_floor(
    api: TestClient,
    as_admin: dict[str, str],
    as_member: dict[str, str],
    roles: None,
    work_org: WorkOrg,
) -> None:
    """BR-P-09. The member is in the owning team, so only visibility can be hiding this."""
    open_project = a_project(api, as_admin, work_org, name="Open", visibility="team")
    secret = a_project(api, as_admin, work_org, name="Secret", visibility="restricted")

    names = {
        item["name"] for item in api.get("/api/v1/projects", headers=as_member).json()["items"]
    }
    assert "Open" in names
    assert "Secret" not in names

    assert api.get(f"/api/v1/projects/{open_project['id']}", headers=as_member).status_code == 200
    hidden = api.get(f"/api/v1/projects/{secret['id']}", headers=as_member)
    assert hidden.status_code == 404
    assert "Secret" not in hidden.text


def test_the_project_lead_reads_their_own_restricted_project(
    api: TestClient, as_admin: dict[str, str], as_member: dict[str, str], roles: None,
    work_org: WorkOrg,
) -> None:
    """The floor in BR-P-09: lead, sponsor, creator, org_admin, auditor."""
    project = a_project(
        api,
        as_admin,
        work_org,
        name="Led",
        visibility="restricted",
        lead_person_id=str(work_org.member),
    )
    assert api.get(f"/api/v1/projects/{project['id']}", headers=as_member).status_code == 200


# --------------------------------------------------------------------------- milestone


def test_milestones_live_under_their_project(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    project = a_project(api, as_admin, work_org)
    created = api.post(
        f"/api/v1/projects/{project['id']}/milestones",
        json={"name": "Cutover", "order_index": 1},
        headers=as_admin,
    )
    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == project["id"]
    assert created.headers["Location"] == f"/api/v1/milestones/{created.json()['id']}"

    listed = api.get(f"/api/v1/projects/{project['id']}/milestones", headers=as_admin).json()
    assert [item["name"] for item in listed["items"]] == ["Cutover"]

    fetched = api.get(f"/api/v1/milestones/{created.json()['id']}", headers=as_admin)
    assert fetched.status_code == 200
    assert fetched.headers["ETag"] == 'W/"1"'


def test_a_milestone_is_unreadable_when_its_project_is(
    api: TestClient, as_admin: dict[str, str], as_member: dict[str, str], roles: None,
    work_org: WorkOrg,
) -> None:
    """BR-P-09: a Milestone carries no visibility of its own, so it borrows its Project's."""
    project = a_project(api, as_admin, work_org, name="Secret", visibility="restricted")
    milestone = api.post(
        f"/api/v1/projects/{project['id']}/milestones",
        json={"name": "Quiet cutover"},
        headers=as_admin,
    ).json()

    hidden = api.get(f"/api/v1/milestones/{milestone['id']}", headers=as_member)
    assert hidden.status_code == 404
    assert "Quiet cutover" not in hidden.text
    assert (
        api.get(f"/api/v1/projects/{project['id']}/milestones", headers=as_member).status_code
        == 404
    )


def test_a_milestone_status_transition_follows_its_state_machine(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    project = a_project(api, as_admin, work_org)
    milestone = api.post(
        f"/api/v1/projects/{project['id']}/milestones",
        json={"name": "Cutover"},
        headers=as_admin,
    ).json()
    path = f"/api/v1/milestones/{milestone['id']}/status"

    bad = api.post(path, json={"target": "achieved"}, headers={**as_admin, "If-Match": 'W/"1"'})
    assert bad.status_code == 422
    assert bad.json()["rule"] == "BR-P-07"

    good = api.post(
        path, json={"target": "in_progress"}, headers={**as_admin, "If-Match": 'W/"1"'}
    )
    assert good.status_code == 200


# --------------------------------------------------------------------------- dependency


def test_a_dependency_is_visible_from_both_ends(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    blocker = api.post("/api/v1/work", json={"title": "First"}, headers=as_admin).json()
    blocked = api.post("/api/v1/work", json={"title": "Second"}, headers=as_admin).json()

    created = api.post(
        "/api/v1/dependencies",
        json={
            "blocker_type": "work",
            "blocker_id": blocker["id"],
            "blocked_type": "work",
            "blocked_id": blocked["id"],
        },
        headers=as_admin,
    )
    assert created.status_code == 201, created.text

    for work in (blocker, blocked):
        listed = api.get(f"/api/v1/work/{work['id']}/dependencies", headers=as_admin).json()
        assert [item["id"] for item in listed["items"]] == [created.json()["id"]]

    # The other endpoint is an id, never a title (BR-W-18 is answered per item, not folded in here).
    body = api.get(f"/api/v1/work/{blocked['id']}/dependencies", headers=as_admin).text
    assert "First" not in body


def test_a_cycle_is_refused_and_the_response_names_the_path(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """BR-D-02, and the reason `find_cycle` has returned a path since Checkpoint 2."""
    first = api.post("/api/v1/work", json={"title": "A"}, headers=as_admin).json()
    second = api.post("/api/v1/work", json={"title": "B"}, headers=as_admin).json()

    def link(blocker: dict, blocked: dict) -> object:
        return api.post(
            "/api/v1/dependencies",
            json={
                "blocker_type": "work",
                "blocker_id": blocker["id"],
                "blocked_type": "work",
                "blocked_id": blocked["id"],
            },
            headers=as_admin,
        )

    assert link(first, second).status_code == 201
    closing = link(second, first)
    assert closing.status_code == 422
    body = closing.json()
    assert body["rule"] == "BR-D-02"
    assert "cycle" in body["detail"]
    assert str(first["id"])[:8] in body["detail"], "the offending path has to be in the message"


def test_a_duplicate_dependency_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    first = api.post("/api/v1/work", json={"title": "A"}, headers=as_admin).json()
    second = api.post("/api/v1/work", json={"title": "B"}, headers=as_admin).json()
    payload = {
        "blocker_type": "work",
        "blocker_id": first["id"],
        "blocked_type": "work",
        "blocked_id": second["id"],
    }
    assert api.post("/api/v1/dependencies", json=payload, headers=as_admin).status_code == 201
    duplicate = api.post("/api/v1/dependencies", json=payload, headers=as_admin)
    assert duplicate.status_code == 422
    assert duplicate.json()["rule"] == "BR-D-06"


def test_withdrawing_a_dependency_keeps_the_row(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """BR-D-05. The row is why the schedule looked the way it did."""
    first = api.post("/api/v1/work", json={"title": "A"}, headers=as_admin).json()
    second = api.post("/api/v1/work", json={"title": "B"}, headers=as_admin).json()
    dependency = api.post(
        "/api/v1/dependencies",
        json={
            "blocker_type": "work",
            "blocker_id": first["id"],
            "blocked_type": "work",
            "blocked_id": second["id"],
        },
        headers=as_admin,
    ).json()

    assert (
        api.post(
            f"/api/v1/dependencies/{dependency['id']}/withdraw", headers=as_admin
        ).status_code
        == 428
    )
    withdrawn = api.post(
        f"/api/v1/dependencies/{dependency['id']}/withdraw",
        headers={**as_admin, "If-Match": f'W/"{dependency["version"]}"'},
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "withdrawn"

    scoped_session.rollback()
    assert (
        scoped_session.execute(
            text("SELECT count(*) FROM dependency WHERE id = :id"),
            {"id": uuid.UUID(dependency["id"])},
        ).scalar_one()
        == 1
    )


def test_a_self_dependency_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    work = api.post("/api/v1/work", json={"title": "Alone"}, headers=as_admin).json()
    response = api.post(
        "/api/v1/dependencies",
        json={
            "blocker_type": "work",
            "blocker_id": work["id"],
            "blocked_type": "work",
            "blocked_id": work["id"],
        },
        headers=as_admin,
    )
    assert response.status_code == 422
    assert response.json()["rule"] == "BR-D-01"


# --------------------------------------------------------------------------- owner


def test_a_creator_claims_unowned_work_through_put_owner(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """D1 still holds on this route: unowned Work is ASSIGN, and capture is self-service."""
    work = api.post("/api/v1/work", json={"title": "Check the IOC API"}, headers=as_member).json()
    claimed = api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.member)},
        headers=as_member,
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["role"] == "OWNER"
    assert claimed.json()["person_id"] == str(work_org.member)


def test_a_member_cannot_take_ownership_from_the_incumbent(
    api: TestClient,
    as_admin: dict[str, str],
    as_member: dict[str, str],
    roles: None,
    work_org: WorkOrg,
) -> None:
    """Occupied means REASSIGN, and REASSIGN denies `member` — one call now, as it did in two.

    The member is put on the work as a CONTRIBUTOR first, and not as scaffolding: without it they
    could not read the Work at all (BR-W-18) and the refusal would be a 404 about visibility rather
    than a 403 about ownership. The interesting case is the person who *is* on the work and still
    may not decide who owns it.
    """
    work = api.post("/api/v1/work", json={"title": "Contested"}, headers=as_admin).json()
    assert (
        api.put(
            f"/api/v1/work/{work['id']}/owner",
            json={"person_id": str(work_org.outsider)},
            headers=as_admin,
        ).status_code
        == 200
    )
    assert (
        api.post(
            f"/api/v1/work/{work['id']}/assignments",
            json={"person_id": str(work_org.member), "role": "CONTRIBUTOR"},
            headers=as_admin,
        ).status_code
        == 201
    )

    response = api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.member)},
        headers=as_member,
    )
    assert response.status_code == 403


def test_a_team_lead_may_move_ownership(
    api: TestClient,
    as_admin: dict[str, str],
    realm: Realm,
    scoped_session: Session,
    roles: None,
    work_org: WorkOrg,
) -> None:
    project = a_project(api, as_admin, work_org)
    api.post(
        f"/api/v1/projects/{project['id']}/status",
        json={"target": "active"},
        headers={**as_admin, "If-Match": 'W/"1"'},
    )
    work = api.post(
        "/api/v1/work",
        json={"title": "Team work", "project_id": project["id"]},
        headers=as_admin,
    ).json()
    api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.outsider)},
        headers=as_admin,
    )

    lead = auth(realm, subject_of(scoped_session, work_org.team_lead), work_org.org_id)
    moved = api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.member)},
        headers=lead,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["person_id"] == str(work_org.member)


def test_moving_ownership_keeps_both_assignments_in_the_history(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-W-14 from the list endpoint. The previous owner stays answerable for their turn."""
    work = api.post("/api/v1/work", json={"title": "Handover"}, headers=as_admin).json()
    api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.member)},
        headers=as_admin,
    )
    api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.outsider)},
        headers=as_admin,
    )

    listed = api.get(f"/api/v1/work/{work['id']}/assignments", headers=as_admin).json()["items"]
    owners = [(item["person_id"], item["status"]) for item in listed]
    assert (str(work_org.member), "ended") in owners
    assert (str(work_org.outsider), "active") in owners
    assert len(listed) == 2


def test_setting_the_current_owner_again_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    work = api.post("/api/v1/work", json={"title": "Stable"}, headers=as_admin).json()
    api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.member)},
        headers=as_admin,
    )
    again = api.put(
        f"/api/v1/work/{work['id']}/owner",
        json={"person_id": str(work_org.member)},
        headers=as_admin,
    )
    assert again.status_code == 422
    assert again.json()["rule"] == "BR-W-13"
