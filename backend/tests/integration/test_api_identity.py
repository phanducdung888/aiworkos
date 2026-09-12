"""The organization directory, read over HTTP.

Small surface, and the tests are mostly about what it refuses. A directory is the one place where
the authorization answer is "everybody in this organization", which makes it the easiest place to
stop checking that the organization boundary is still there.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.ids import uuid7
from tests.integration.conftest import Realm, WorkOrg, auth, subject_of

pytestmark = pytest.mark.integration


def test_me_answers_the_two_questions_the_token_does_not(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """Which organization, and what may I do in it (security-model §2)."""
    body = api.get("/api/v1/me", headers=as_member).json()
    assert body["person_id"] == str(work_org.member)
    assert body["org_id"] == str(work_org.org_id)
    assert body["display_name"] == "Mira Member"
    assert body["roles"] == ["member"]


def test_me_reflects_the_organization_in_the_header_not_the_token(
    api: TestClient,
    realm: Realm,
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
    app_session_factory: sessionmaker[Session],
) -> None:
    """The same human in two organizations is two Person rows (security-model §2, PQ-2).

    This is the case a token claim would get wrong, and the reason the header exists.
    """
    subject = subject_of(scoped_session, work_org.member)
    second_org, second_person = uuid7(), uuid7()
    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(second_org)}
    )
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:i, 'Second', :s)"),
        {"i": second_org, "s": f"second-{uuid.uuid4().hex[:10]}"},
    )
    session.execute(
        text(
            "INSERT INTO person (id, org_id, display_name, keycloak_subject, status) "
            "VALUES (:i, :o, 'Mira Elsewhere', :k, 'active')"
        ),
        {"i": second_person, "o": second_org, "k": subject},
    )
    session.execute(
        text(
            "INSERT INTO organization_membership (id, org_id, person_id, status) "
            "VALUES (:i, :o, :p, 'active')"
        ),
        {"i": uuid7(), "o": second_org, "p": second_person},
    )
    session.execute(
        text(
            "INSERT INTO role_assignment (id, org_id, person_id, role, scope_type) "
            "VALUES (:i, :o, :p, 'org_admin', 'organization')"
        ),
        {"i": uuid7(), "o": second_org, "p": second_person},
    )
    session.commit()
    session.close()

    here = api.get("/api/v1/me", headers=auth(realm, subject, work_org.org_id)).json()
    there = api.get("/api/v1/me", headers=auth(realm, subject, second_org)).json()

    assert here["person_id"] != there["person_id"], "one subject, one Person per organization"
    assert here["display_name"] == "Mira Member"
    assert there["display_name"] == "Mira Elsewhere"
    assert here["roles"] == ["member"]
    assert there["roles"] == ["org_admin"]


def test_the_people_directory_is_readable_by_an_ordinary_member(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """PERSON.LIST grants every role an ORG condition. A directory nobody can read is not one."""
    listed = api.get("/api/v1/people", headers=as_member).json()["items"]
    names = {p["display_name"] for p in listed}
    assert {"Avery Admin", "Mira Member", "Tomas Team"} <= names


def test_departed_people_are_out_of_the_picker_but_still_addressable(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-I-05: they cannot take new work, so offering them is offering a refusal.

    Still fetchable by id, because "who owned this in March" is a question about somebody who has
    since left.
    """
    listed = api.get("/api/v1/people", headers=as_admin).json()["items"]
    default = {p["display_name"] for p in listed}
    assert "Devi Departed" not in default

    widened = api.get("/api/v1/people?include_departed=true", headers=as_admin).json()
    assert "Devi Departed" in {p["display_name"] for p in widened["items"]}

    direct = api.get(f"/api/v1/people/{work_org.departed}", headers=as_admin)
    assert direct.status_code == 200
    assert direct.json()["status"] == "departed"


def test_the_directory_search_narrows_without_widening(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    found = api.get("/api/v1/people?q=Mira", headers=as_admin).json()["items"]
    assert [p["display_name"] for p in found] == ["Mira Member"]
    assert api.get("/api/v1/people?q=nobody-by-that-name", headers=as_admin).json()["items"] == []


def test_teams_and_departments_are_listable_and_linked(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The lookup a client needs before it can satisfy BR-P-01 on `POST /projects`."""
    teams = api.get("/api/v1/teams", headers=as_member).json()["items"]
    platform = next(t for t in teams if t["name"] == "Platform")
    assert platform["id"] == str(work_org.team_id)
    assert platform["department_id"] == str(work_org.department_id)
    assert platform["lead_person_id"] == str(work_org.team_lead)

    departments = api.get("/api/v1/departments", headers=as_member).json()["items"]
    assert [d["name"] for d in departments] == ["Delivery"]

    scoped = api.get(
        f"/api/v1/teams?department_id={work_org.department_id}", headers=as_member
    ).json()["items"]
    assert {t["name"] for t in scoped} == {"Platform"}


def test_the_directory_of_another_organization_is_not_visible(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    app_session_factory: sessionmaker[Session],
) -> None:
    """The easiest boundary to forget, because a directory is meant to be open."""
    other_org, stranger = uuid7(), uuid7()
    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(other_org)}
    )
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:i, 'Other', :s)"),
        {"i": other_org, "s": f"other-{uuid.uuid4().hex[:10]}"},
    )
    session.execute(
        text(
            "INSERT INTO person (id, org_id, display_name, keycloak_subject, status) "
            "VALUES (:i, :o, 'Secret Stranger', :k, 'active')"
        ),
        {"i": stranger, "o": other_org, "k": f"sub-{uuid.uuid4().hex[:10]}"},
    )
    session.commit()
    session.close()

    listed = api.get("/api/v1/people?limit=200", headers=as_admin)
    assert "Secret Stranger" not in listed.text
    assert all(p["id"] != str(stranger) for p in listed.json()["items"])

    direct = api.get(f"/api/v1/people/{stranger}", headers=as_admin)
    assert direct.status_code == 404
    assert "Secret Stranger" not in direct.text
    assert direct.json()["detail"] == "No such resource is visible in this organization."


def test_the_directory_needs_a_principal_like_everything_else(
    api: TestClient, work_org: WorkOrg
) -> None:
    assert api.get("/api/v1/people").status_code == 400  # no organization header
    assert (
        api.get("/api/v1/people", headers={"X-Organization-Id": str(work_org.org_id)}).status_code
        == 401
    )


def test_the_directory_pages_like_the_rest_of_the_api(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    first = api.get("/api/v1/people?limit=2", headers=as_admin).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None

    seen = [p["id"] for p in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = api.get(f"/api/v1/people?limit=2&cursor={cursor}", headers=as_admin).json()
        seen.extend(p["id"] for p in page["items"])
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen))
    assert api.get("/api/v1/people?cursor=nonsense", headers=as_admin).status_code == 400
