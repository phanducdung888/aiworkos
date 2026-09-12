"""What a list must not reveal.

Read authorization and BR-W-18 visibility, asserted through HTTP because that is where a leak would
actually reach somebody. Two properties matter and they are different:

* a Work the caller may not read is absent from the list;
* its absence is not observable — no count, no gap, no short page that says "seven were removed".

The second is the one that is easy to lose. Fetching a page and then filtering it satisfies the
first and fails the second: a page of 5 that returns 3 has just reported that 2 exist.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.ids import uuid7
from tests.integration.conftest import Realm, WorkOrg, auth, grant, subject_of

pytestmark = pytest.mark.integration


def make_work(
    session: Session,
    org_id: uuid.UUID,
    *,
    title: str,
    visibility: str = "team",
    project_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> uuid.UUID:
    work_id = uuid7()
    session.execute(
        text(
            "INSERT INTO work (id, org_id, title, status, visibility, project_id, "
            "created_by_person_id) "
            "VALUES (:id, :org, :title, 'todo', :visibility, :project, :creator)"
        ),
        {
            "id": work_id,
            "org": org_id,
            "title": title,
            "visibility": visibility,
            "project": project_id,
            "creator": created_by,
        },
    )
    session.commit()
    return work_id


def make_project(
    session: Session, org_id: uuid.UUID, *, team_id: uuid.UUID | None, name: str = "Migration"
) -> uuid.UUID:
    project_id = uuid7()
    session.execute(
        text(
            "INSERT INTO project (id, org_id, name, owning_team_id, department_id, status) "
            "VALUES (:id, :org, :name, :team, :dept, 'active')"
        ),
        {
            "id": project_id,
            "org": org_id,
            "name": name,
            "team": team_id,
            "dept": None if team_id else uuid7(),
        },
    )
    session.commit()
    return project_id


@pytest.fixture
def world(scoped_session: Session, work_org: WorkOrg) -> dict[str, uuid.UUID]:
    """One Work at each visibility level, plus the two ownership shapes that cut across them."""
    grant(scoped_session, work_org.org_id, work_org.admin, "org_admin")
    grant(scoped_session, work_org.org_id, work_org.member, "member")
    grant(scoped_session, work_org.org_id, work_org.outsider, "member")
    grant(scoped_session, work_org.org_id, work_org.team_lead, "team_lead")
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "department_lead")

    team_project = make_project(scoped_session, work_org.org_id, team_id=work_org.team_id)
    other_project = make_project(
        scoped_session, work_org.org_id, team_id=work_org.other_team_id, name="Unrelated"
    )
    return {
        "org_wide": make_work(
            scoped_session,
            work_org.org_id,
            title="Org wide",
            visibility="organization",
            project_id=other_project,
        ),
        "team_work": make_work(
            scoped_session,
            work_org.org_id,
            title="Team work",
            visibility="team",
            project_id=team_project,
        ),
        "other_team_work": make_work(
            scoped_session,
            work_org.org_id,
            title="Another team's work",
            visibility="team",
            project_id=other_project,
        ),
        "restricted": make_work(
            scoped_session,
            work_org.org_id,
            title="Restricted matter",
            visibility="restricted",
            project_id=team_project,
        ),
        "captured": make_work(
            scoped_session,
            work_org.org_id,
            title="Captured by the member",
            visibility="team",
            created_by=work_org.member,
        ),
        "someone_elses_note": make_work(
            scoped_session,
            work_org.org_id,
            title="Captured by somebody else",
            visibility="team",
            created_by=work_org.outsider,
        ),
    }


def titles_visible_to(
    api: TestClient, realm: Realm, session: Session, org_id: uuid.UUID, person_id: uuid.UUID
) -> set[str]:
    headers = auth(realm, subject_of(session, person_id), org_id)
    return {
        item["title"]
        for item in api.get("/api/v1/work?limit=200", headers=headers).json()["items"]
    }


def test_an_administrator_sees_every_partition_and_every_visibility(
    api: TestClient, realm: Realm, scoped_session: Session, work_org: WorkOrg, world: dict
) -> None:
    """BR-W-18's exemption. An admin who could not see restricted Work could widen it and look."""
    visible = titles_visible_to(api, realm, scoped_session, work_org.org_id, work_org.admin)
    assert "Restricted matter" in visible
    assert "Another team's work" in visible
    assert "Captured by somebody else" in visible


def test_a_member_sees_their_team_their_captures_and_nothing_restricted(
    api: TestClient, realm: Realm, scoped_session: Session, work_org: WorkOrg, world: dict
) -> None:
    """The core of BR-W-18, from the seat that has the least reach."""
    visible = titles_visible_to(api, realm, scoped_session, work_org.org_id, work_org.member)

    assert "Team work" in visible, "a member reaches work in their own team's project"
    assert "Captured by the member" in visible, "and work they captured themselves"

    assert "Restricted matter" not in visible, "restricted admits only the floor (BR-W-18)"
    assert "Another team's work" not in visible, "team visibility does not cross teams"
    assert "Captured by somebody else" not in visible, (
        "non-project work at team visibility resolves to its floor: creator and assignees only"
    )
    # `Org wide` carries `visibility = organization` and is still absent, which is the rule working
    # rather than failing: visibility narrows and never widens (BR-W-18). The work sits in another
    # team's project, so a member has no role reach to it, and an open visibility grants none.
    assert "Org wide" not in visible


def test_visibility_narrows_a_role_that_otherwise_reads_everything(
    api: TestClient, realm: Realm, scoped_session: Session, work_org: WorkOrg, world: dict
) -> None:
    """The half of BR-W-18 that role reach alone cannot express.

    `executive` holds an ORG grant on `WORK.READ`, so authorization by itself would hand them the
    whole organization. They are not among the roles visibility exempts, so the level on each row
    still applies — which is what makes visibility a control rather than a label.
    """
    grant(scoped_session, work_org.org_id, work_org.outsider, "executive")
    visible = titles_visible_to(api, realm, scoped_session, work_org.org_id, work_org.outsider)

    assert "Org wide" in visible, "organization visibility admits any reader with reach"
    assert "Team work" not in visible, "team visibility excludes a reader outside the team"
    assert "Restricted matter" not in visible, "and restricted excludes everyone but the floor"


def test_an_assignment_admits_a_reader_at_every_level(
    api: TestClient,
    realm: Realm,
    scoped_session: Session,
    work_org: WorkOrg,
    world: dict,
) -> None:
    """The floor in BR-W-18. Being on the work is the one relation no level overrides."""
    before = titles_visible_to(api, realm, scoped_session, work_org.org_id, work_org.outsider)
    assert "Restricted matter" not in before

    scoped_session.execute(
        text(
            "INSERT INTO work_assignment "
            "(id, org_id, work_id, person_id, role, status, assigned_by_actor) "
            "VALUES (:id, :org, :work, :person, 'CONTRIBUTOR', 'active', '{}'::jsonb)"
        ),
        {
            "id": uuid7(),
            "org": work_org.org_id,
            "work": world["restricted"],
            "person": work_org.outsider,
        },
    )
    scoped_session.commit()

    after = titles_visible_to(api, realm, scoped_session, work_org.org_id, work_org.outsider)
    assert "Restricted matter" in after


def test_a_department_lead_reaches_through_the_teams_below_them(
    api: TestClient, realm: Realm, scoped_session: Session, work_org: WorkOrg, world: dict
) -> None:
    visible = titles_visible_to(api, realm, scoped_session, work_org.org_id, work_org.dept_lead)
    assert "Team work" in visible, "the team sits in the department they lead"
    assert "Another team's work" not in visible, "the other team does not"
    assert "Restricted matter" not in visible, "and department reach does not open restricted work"


def test_a_hidden_row_is_not_observable_through_the_page_size(
    api: TestClient, realm: Realm, scoped_session: Session, work_org: WorkOrg, world: dict
) -> None:
    """Test 3, second half. Filtering happens in the WHERE clause, not after the page is cut."""
    headers = auth(
        realm, subject_of(scoped_session, work_org.member), work_org.org_id
    )
    # Enough readable rows that a limit of 3 has to fill from them rather than from what is hidden.
    for index in range(6):
        assert (
            api.post(
                "/api/v1/work", json={"title": f"Mine {index}"}, headers=headers
            ).status_code
            == 201
        )

    page = api.get("/api/v1/work?limit=3", headers=headers).json()
    assert len(page["items"]) == 3, "a page must be full of rows the caller may see"
    assert page["next_cursor"] is not None
    assert all("Restricted" not in item["title"] for item in page["items"])

    everything: list[str] = []
    cursor = None
    while True:
        query = "?limit=3" + (f"&cursor={cursor}" if cursor else "")
        body = api.get(f"/api/v1/work{query}", headers=headers).json()
        everything.extend(item["title"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert "Restricted matter" not in everything
    assert "Another team's work" not in everything
    assert len(everything) == len(set(everything))


def test_an_unreadable_work_item_reads_as_missing_rather_than_forbidden(
    api: TestClient, realm: Realm, scoped_session: Session, work_org: WorkOrg, world: dict
) -> None:
    """403 would confirm it exists. List and single read agree because they share one query."""
    headers = auth(realm, subject_of(scoped_session, work_org.member), work_org.org_id)
    response = api.get(f"/api/v1/work/{world['restricted']}", headers=headers)
    assert response.status_code == 404
    assert "Restricted matter" not in response.text


def test_the_owner_filter_cannot_be_used_to_probe_unreadable_work(
    api: TestClient, realm: Realm, scoped_session: Session, work_org: WorkOrg, world: dict
) -> None:
    """A filter narrows what the caller may already see; it never reaches past it."""
    scoped_session.execute(
        text(
            "INSERT INTO work_assignment "
            "(id, org_id, work_id, person_id, role, status, assigned_by_actor) "
            "VALUES (:id, :org, :work, :person, 'OWNER', 'active', '{}'::jsonb)"
        ),
        {
            "id": uuid7(),
            "org": work_org.org_id,
            "work": world["restricted"],
            "person": work_org.admin,
        },
    )
    scoped_session.commit()

    headers = auth(realm, subject_of(scoped_session, work_org.member), work_org.org_id)
    found = api.get(
        f"/api/v1/work?owner_person_id={work_org.admin}", headers=headers
    ).json()["items"]
    assert all(item["title"] != "Restricted matter" for item in found)
