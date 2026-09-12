"""W-11: what happens when Work Core is handed an Identity id that is not there.

Before ADR-0035 the answer was a composite foreign key violation surfacing as a 500, because the
reference was only checked by the database and psycopg's error arrived too late to say anything
useful. Work Core now resolves every Person, Team and Department through `identity.public` before
it writes, so the answer is a problem document naming the field and the rule.

The reason this is worth its own module rather than a line in the Work tests: the interesting cases
are all ones where the row *exists* — in another organization, or archived, or departed — and each
of those is a different wrong answer if the check is written carelessly. A plain `SELECT ... WHERE
id = ?` gets the first one wrong.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration


def test_an_unknown_team_is_a_rule_violation_not_a_constraint_violation(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    response = api.post(
        "/api/v1/projects",
        json={"name": "Migration", "owning_team_id": str(uuid.uuid4())},
        headers=as_admin,
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert "BR-G-01" in response.text
    # The field has to be named. "Something was invalid" is not an error a caller can act on.
    assert "owning_team_id" in response.text, body


def test_another_organizations_team_cannot_own_a_project(
    api: TestClient, as_admin: dict[str, str], roles: None,
    app_session_factory, scoped_session: Session,
) -> None:
    """The row exists and is perfectly valid — just not here.

    This is the case a naive existence check passes and must not: the foreign key is satisfied,
    so only a tenant-aware lookup catches it.
    """
    from app.platform.ids import uuid7

    other_org, other_team = uuid7(), uuid7()
    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(other_org)}
    )
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Rival', :slug)"),
        {"id": other_org, "slug": f"rival-{uuid.uuid4().hex[:12]}"},
    )
    session.execute(
        text(
            "INSERT INTO team (id, org_id, name, status) VALUES (:id, :org, 'Theirs', 'active')"
        ),
        {"id": other_team, "org": other_org},
    )
    session.commit()
    session.close()

    response = api.post(
        "/api/v1/projects",
        json={"name": "Migration", "owning_team_id": str(other_team)},
        headers=as_admin,
    )
    assert response.status_code == 422
    assert "BR-G-01" in response.text


def test_an_archived_team_can_still_be_given_new_work_today(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """Current behaviour, pinned deliberately rather than asserted as desirable.

    `assert_team_exists` checks that the team is in this organization; it does not check status. So
    a team archived this morning can be handed a new Project this afternoon. BR-I-05 does the
    opposite for people — a departed Person takes no new work — and no rule states the analogue for
    an archived unit, so the asymmetry is undecided rather than settled.

    The test is here so that deciding it is a visible change to a stated expectation, instead of a
    silent one nobody notices. If the rule is added, this test is the one that should fail.
    """
    read = api.get(f"/api/v1/teams/{work_org.other_team_id}", headers=as_admin)
    archived = api.post(
        f"/api/v1/teams/{work_org.other_team_id}/status",
        json={"target": "archived"},
        headers={**as_admin, "If-Match": read.headers["ETag"]},
    )
    assert archived.status_code == 200, archived.text

    response = api.post(
        "/api/v1/projects",
        json={"name": "Migration", "owning_team_id": str(work_org.other_team_id)},
        headers=as_admin,
    )
    assert response.status_code == 201, response.text


def test_an_unknown_person_cannot_be_assigned(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    work = api.post("/api/v1/work", json={"title": "Something"}, headers=as_admin).json()
    response = api.post(
        f"/api/v1/work/{work['id']}/assignments",
        json={"person_id": str(uuid.uuid4()), "role": "CONTRIBUTOR"},
        headers=as_admin,
    )
    assert response.status_code == 422
    assert "BR-G-01" in response.text
    assert "person_id" in response.text


def test_no_reference_failure_reaches_the_client_as_a_server_error(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """The whole point of W-11. Every one of these was a 500 before ADR-0035."""
    absent = str(uuid.uuid4())
    for payload in (
        {"name": "P", "owning_team_id": absent},
        {"name": "P", "owning_department_id": absent},
    ):
        response = api.post("/api/v1/projects", json=payload, headers=as_admin)
        assert response.status_code != 500, f"{payload} produced a server error: {response.text}"
        assert response.status_code == 422, response.text
