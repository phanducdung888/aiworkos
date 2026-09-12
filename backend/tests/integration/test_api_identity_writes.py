"""The Identity write side over HTTP.

The read side is covered in `test_api_identity.py`. This asks the four questions of the writes:
does the mutation take the whole path (authorize, domain, repository, audit, outbox), is
authorization narrow where ADR-0036 and ADR-0037 say it is narrow, does the concurrency story hold,
and does another organization's row look like nothing at all.

The BR-I rules themselves are asserted in `tests/unit/test_identity_domain.py`, without a database.
What is asserted here is that the HTTP path actually reaches them and renders the refusal as a
problem document naming the rule, rather than letting a constraint violation escape as a 500.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import Realm, WorkOrg, auth, grant, subject_of

pytestmark = pytest.mark.integration


def a_person(api: TestClient, headers: dict[str, str], **over: object) -> dict:
    payload: dict[str, object] = {"display_name": "New Hire"}
    payload.update(over)
    response = api.post("/api/v1/people", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def etag(response) -> str:
    tag = response.headers.get("ETag")
    assert tag, "a versioned resource must return an ETag"
    return tag


def audit_rows(session: Session, resource_id: uuid.UUID) -> list[dict]:
    session.rollback()
    return [
        dict(row)
        for row in session.execute(
            text(
                "SELECT action, resource_type, authorization_context, actor "
                "FROM audit_entry WHERE resource_id = :id ORDER BY occurred_at"
            ),
            {"id": resource_id},
        )
        .mappings()
        .all()
    ]


def outbox_types(session: Session, aggregate_id: uuid.UUID) -> list[str]:
    session.rollback()
    return [
        row[0]
        for row in session.execute(
            text("SELECT type FROM outbox WHERE aggregate_id = :id ORDER BY occurred_at"),
            {"id": aggregate_id},
        ).all()
    ]


# --------------------------------------------------------------------------- person


def test_creating_a_person_takes_the_whole_path(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    person = a_person(api, as_admin, display_name="Nga Nguyen", email="nga@example.test")
    assert person["status"] == "active"

    entries = audit_rows(scoped_session, uuid.UUID(person["id"]))
    assert [e["action"] for e in entries] == ["create"]
    assert entries[0]["resource_type"] == "person"
    assert entries[0]["actor"]["person_id"] == str(work_org.admin)
    assert entries[0]["authorization_context"]["role"] == "org_admin"
    assert outbox_types(scoped_session, uuid.UUID(person["id"])) == ["PersonCreated"]


def test_a_person_who_will_never_sign_in_can_still_be_created(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """BR-I-04. A contractor named as a committer owns work and holds no credential.

    The subject is checked in the database rather than in the response, because `PersonResource`
    does not publish it: which credential a Person authenticates with is the authentication
    system's business, and the directory is readable by everybody in the organization.
    """
    person = a_person(api, as_admin, display_name="Contractor")
    assert person["status"] == "active"
    assert "keycloak_subject" not in person

    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT keycloak_subject FROM person WHERE id = :id"),
        {"id": uuid.UUID(person["id"])},
    ).scalar_one() is None


def test_a_member_cannot_create_a_person(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """ADR-0036: onboarding is administrative. There is no self-service path."""
    response = api.post("/api/v1/people", json={"display_name": "Myself"}, headers=as_member)
    assert response.status_code == 403


def test_an_invalid_email_is_a_problem_document_not_a_constraint_violation(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    response = api.post(
        "/api/v1/people",
        json={"display_name": "Nga", "email": "not-an-address"},
        headers=as_admin,
    )
    assert response.status_code == 422
    assert "BR-I-04" in response.text


def test_departing_somebody_stops_new_work_reaching_them(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-I-05. What it must not do is disturb the work they already own."""
    person = a_person(api, as_admin)
    read = api.get(f"/api/v1/people/{person['id']}", headers=as_admin)
    response = api.post(
        f"/api/v1/people/{person['id']}/status",
        json={"target": "departed"},
        headers={**as_admin, "If-Match": etag(read)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "departed"

    work = api.post("/api/v1/work", json={"title": "Something new"}, headers=as_admin)
    assert work.status_code == 201, work.text
    refused = api.post(
        f"/api/v1/work/{work.json()['id']}/assignments",
        json={"person_id": person["id"], "role": "CONTRIBUTOR"},
        headers=as_admin,
    )
    assert refused.status_code == 422
    assert "BR-I-05" in refused.text


def test_departure_is_not_undone_by_flipping_a_field(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    read = api.get(f"/api/v1/people/{work_org.departed}", headers=as_admin)
    response = api.post(
        f"/api/v1/people/{work_org.departed}/status",
        json={"target": "active"},
        headers={**as_admin, "If-Match": etag(read)},
    )
    assert response.status_code == 422
    assert "BR-I-05" in response.text


def test_a_stale_version_loses(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    person = a_person(api, as_admin)
    stale = etag(api.get(f"/api/v1/people/{person['id']}", headers=as_admin))

    first = api.patch(
        f"/api/v1/people/{person['id']}",
        json={"display_name": "First Writer"},
        headers={**as_admin, "If-Match": stale},
    )
    assert first.status_code == 200

    second = api.patch(
        f"/api/v1/people/{person['id']}",
        json={"display_name": "Second Writer"},
        headers={**as_admin, "If-Match": stale},
    )
    assert second.status_code == 412


def test_a_write_without_if_match_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    person = a_person(api, as_admin)
    response = api.patch(
        f"/api/v1/people/{person['id']}", json={"display_name": "X"}, headers=as_admin
    )
    assert response.status_code == 428


def test_the_same_idempotency_key_creates_one_person(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    headers = {**as_admin, "Idempotency-Key": uuid.uuid4().hex}
    body = {"display_name": "Only Once"}
    first = api.post("/api/v1/people", json=body, headers=headers)
    second = api.post("/api/v1/people", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    scoped_session.rollback()
    count = scoped_session.execute(
        text("SELECT count(*) FROM person WHERE display_name = 'Only Once'")
    ).scalar_one()
    assert count == 1


# --------------------------------------------------------------------------- department


def test_a_department_cycle_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """BR-I-01. The database trigger refuses this too; the API must name the rule first."""
    parent = api.post("/api/v1/departments", json={"name": "Parent"}, headers=as_admin).json()
    child = api.post(
        "/api/v1/departments",
        json={"name": "Child", "parent_department_id": parent["id"]},
        headers=as_admin,
    ).json()

    read = api.get(f"/api/v1/departments/{parent['id']}", headers=as_admin)
    response = api.patch(
        f"/api/v1/departments/{parent['id']}",
        json={"parent_department_id": child["id"]},
        headers={**as_admin, "If-Match": etag(read)},
    )
    assert response.status_code == 422
    assert "BR-I-01" in response.text


def test_a_department_cannot_be_nested_past_the_limit(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    parent: str | None = None
    for depth in range(5):
        body: dict[str, object] = {"name": f"Level {depth}"}
        if parent:
            body["parent_department_id"] = parent
        created = api.post("/api/v1/departments", json=body, headers=as_admin)
        assert created.status_code == 201, f"depth {depth}: {created.text}"
        parent = created.json()["id"]

    too_deep = api.post(
        "/api/v1/departments",
        json={"name": "Level 5", "parent_department_id": parent},
        headers=as_admin,
    )
    assert too_deep.status_code == 422
    assert "BR-I-01" in too_deep.text


def test_a_department_lead_edits_inside_their_own_subtree_only(
    api: TestClient, realm: Realm, roles: None, work_org: WorkOrg, scoped_session: Session
) -> None:
    grant(
        scoped_session,
        work_org.org_id,
        work_org.dept_lead,
        "department_lead",
        scope_type="department",
        scope_id=work_org.department_id,
    )
    headers = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)

    mine = api.get(f"/api/v1/departments/{work_org.department_id}", headers=headers)
    renamed = api.patch(
        f"/api/v1/departments/{work_org.department_id}",
        json={"name": "Delivery, renamed"},
        headers={**headers, "If-Match": etag(mine)},
    )
    assert renamed.status_code == 200, renamed.text

    admin_headers = auth(realm, subject_of(scoped_session, work_org.admin), work_org.org_id)
    other = api.post(
        "/api/v1/departments", json={"name": "Finance"}, headers=admin_headers
    ).json()
    theirs = api.get(f"/api/v1/departments/{other['id']}", headers=headers)
    refused = api.patch(
        f"/api/v1/departments/{other['id']}",
        json={"name": "Finance, renamed"},
        headers={**headers, "If-Match": etag(theirs)},
    )
    assert refused.status_code == 403


def test_archiving_a_department_does_not_delete_it(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """BR-G-04. The row stays, so the history that points at it stays readable."""
    created = api.post("/api/v1/departments", json={"name": "Sunset"}, headers=as_admin).json()
    read = api.get(f"/api/v1/departments/{created['id']}", headers=as_admin)
    archived = api.post(
        f"/api/v1/departments/{created['id']}/status",
        json={"target": "archived"},
        headers={**as_admin, "If-Match": etag(read)},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    scoped_session.rollback()
    still_there = scoped_session.execute(
        text("SELECT status FROM department WHERE id = :id"), {"id": uuid.UUID(created["id"])}
    ).scalar_one()
    assert still_there == "archived"


# --------------------------------------------------------------------------- team membership


def test_a_team_lead_manages_their_own_team(
    api: TestClient, realm: Realm, roles: None, work_org: WorkOrg, scoped_session: Session
) -> None:
    grant(
        scoped_session,
        work_org.org_id,
        work_org.team_lead,
        "team_lead",
        scope_type="team",
        scope_id=work_org.team_id,
    )
    headers = auth(realm, subject_of(scoped_session, work_org.team_lead), work_org.org_id)

    added = api.post(
        f"/api/v1/teams/{work_org.team_id}/members",
        json={"person_id": str(work_org.outsider)},
        headers=headers,
    )
    assert added.status_code == 201, added.text

    refused = api.post(
        f"/api/v1/teams/{work_org.other_team_id}/members",
        json={"person_id": str(work_org.outsider)},
        headers=headers,
    )
    assert refused.status_code == 403


def test_joining_a_team_twice_is_refused_and_rejoining_is_not(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """BR-I-03. One open membership at a time; the closed ones stay and must not block."""
    first = api.post(
        f"/api/v1/teams/{work_org.team_id}/members",
        json={"person_id": str(work_org.outsider)},
        headers=as_admin,
    )
    assert first.status_code == 201

    again = api.post(
        f"/api/v1/teams/{work_org.team_id}/members",
        json={"person_id": str(work_org.outsider)},
        headers=as_admin,
    )
    assert again.status_code == 422
    assert "BR-I-03" in again.text

    read = api.get(f"/api/v1/teams/{work_org.team_id}/members", headers=as_admin)
    row = next(m for m in read.json()["items"] if m["id"] == first.json()["id"])
    ended = api.delete(
        f"/api/v1/teams/{work_org.team_id}/members/{row['id']}",
        headers={**as_admin, "If-Match": f'W/"{row["version"]}"'},
    )
    assert ended.status_code == 200
    assert ended.json()["valid_to"] is not None

    rejoined = api.post(
        f"/api/v1/teams/{work_org.team_id}/members",
        json={"person_id": str(work_org.outsider)},
        headers=as_admin,
    )
    assert rejoined.status_code == 201


def test_ending_a_membership_keeps_the_row(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    listed = api.get(f"/api/v1/teams/{work_org.team_id}/members", headers=as_admin).json()
    row = listed["items"][0]
    api.delete(
        f"/api/v1/teams/{work_org.team_id}/members/{row['id']}",
        headers={**as_admin, "If-Match": f'W/"{row["version"]}"'},
    )
    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM team_membership WHERE id = :id"),
        {"id": uuid.UUID(row["id"])},
    ).scalar_one() == 1


def test_a_departed_person_cannot_be_added_to_a_team(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    response = api.post(
        f"/api/v1/teams/{work_org.team_id}/members",
        json={"person_id": str(work_org.departed)},
        headers=as_admin,
    )
    assert response.status_code == 422
    assert "BR-I-05" in response.text


# --------------------------------------------------------------------------- roles


def test_only_an_admin_grants_a_role(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """There is no path by which a role grants itself a wider one."""
    response = api.post(
        f"/api/v1/people/{work_org.member}/roles",
        json={"role": "org_admin"},
        headers=as_member,
    )
    assert response.status_code == 403


def test_a_narrow_role_must_name_its_scope(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    response = api.post(
        f"/api/v1/people/{work_org.member}/roles",
        json={"role": "department_lead", "scope_type": "department"},
        headers=as_admin,
    )
    assert response.status_code == 422


def test_revoking_a_role_keeps_the_history(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    granted = api.post(
        f"/api/v1/people/{work_org.outsider}/roles",
        json={"role": "team_lead", "scope_type": "team", "scope_id": str(work_org.team_id)},
        headers=as_admin,
    )
    assert granted.status_code == 201, granted.text
    row = granted.json()

    revoked = api.delete(
        f"/api/v1/roles/{row['id']}",
        headers={**as_admin, "If-Match": f'W/"{row["version"]}"'},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    listed = api.get(f"/api/v1/people/{work_org.outsider}/roles", headers=as_admin).json()
    assert any(r["id"] == row["id"] for r in listed["items"]), "a revoked grant is still history"


# --------------------------------------------------------------------------- external identity


def test_an_external_identity_is_created_unconfirmed(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """ADR-0037. Claiming a handle and being believed about it are separate acts."""
    created = api.post(
        f"/api/v1/people/{work_org.member}/external-identities",
        json={"source_system": "whatsapp", "external_id": "+84900000001", "confidence": 90},
        headers=as_admin,
    )
    assert created.status_code == 201, created.text
    assert created.json()["confirmed_at"] is None


def test_confirming_records_who_decided_and_when(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    created = api.post(
        f"/api/v1/people/{work_org.member}/external-identities",
        json={"source_system": "slack", "external_id": "U123", "confidence": 90},
        headers=as_admin,
    ).json()
    confirmed = api.post(
        f"/api/v1/external-identities/{created['id']}/confirm",
        headers={**as_admin, "If-Match": f'W/"{created["version"]}"'},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed_at"] is not None
    assert confirmed.json()["confirmed_by_person_id"] == str(work_org.admin)


def test_nobody_confirms_their_own_handle(
    api: TestClient, as_member: dict[str, str], as_admin: dict[str, str], roles: None,
    work_org: WorkOrg,
) -> None:
    """ADR-0037: no SELF grant anywhere on this resource. A confirmed mapping is an authority."""
    created = api.post(
        f"/api/v1/people/{work_org.member}/external-identities",
        json={"source_system": "whatsapp", "external_id": "+84900000009", "confidence": 90},
        headers=as_admin,
    ).json()
    response = api.post(
        f"/api/v1/external-identities/{created['id']}/confirm",
        headers={**as_member, "If-Match": f'W/"{created["version"]}"'},
    )
    assert response.status_code == 403


def test_one_handle_maps_to_one_person(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    body = {"source_system": "whatsapp", "external_id": "+84900000002", "confidence": 50}
    first = api.post(
        f"/api/v1/people/{work_org.member}/external-identities", json=body, headers=as_admin
    )
    assert first.status_code == 201
    second = api.post(
        f"/api/v1/people/{work_org.outsider}/external-identities", json=body, headers=as_admin
    )
    assert second.status_code == 422
    assert "BR-I-07" in second.text


def test_a_member_cannot_read_the_mapping_table_through_a_write_endpoint(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    response = api.post(
        f"/api/v1/people/{work_org.member}/external-identities",
        json={"source_system": "whatsapp", "external_id": "+84900000003"},
        headers=as_member,
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- tenancy


def test_another_organizations_person_looks_like_nothing_at_all(
    api: TestClient, as_admin: dict[str, str], roles: None, other_org_person: uuid.UUID
) -> None:
    read = api.get(f"/api/v1/people/{other_org_person}", headers=as_admin)
    assert read.status_code == 404

    write = api.patch(
        f"/api/v1/people/{other_org_person}",
        json={"display_name": "Reached Across"},
        headers={**as_admin, "If-Match": 'W/"1"'},
    )
    assert write.status_code == 404, "a cross-tenant write must not distinguish itself from absence"
