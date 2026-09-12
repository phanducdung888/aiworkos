"""`/api/v1/work` over HTTP.

The point of these is not that the handlers return the right shape. It is that the HTTP path is the
same path: a mutation made through a router must leave exactly the audit entry and outbox event it
leaves when a service is called directly, and a read must be filtered by the same authorization the
service would apply. A router that skipped a step would still look correct in a response body, which
is why the assertions below reach past the response and into the tables.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.ids import uuid7
from tests.integration.conftest import Realm, WorkOrg, subject_of

pytestmark = pytest.mark.integration

PROBLEM = "application/problem+json"


# --------------------------------------------------------------------------- the spine


def test_health_needs_no_token(api: TestClient) -> None:
    """A liveness probe that needed an identity provider could not report on the process alone."""
    assert api.get("/health").json() == {"status": "ok"}


def test_creating_work_over_http_writes_the_same_audit_and_event_as_the_service(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """Test 7. The router is a translation layer, not a second way in."""
    response = api.post(
        "/api/v1/work", json={"title": "Check the IOC API"}, headers=as_member
    )
    assert response.status_code == 201, response.text
    body = response.json()
    work_id = uuid.UUID(body["id"])

    assert body["project_id"] is None
    assert body["status"] == "todo"
    assert response.headers["ETag"] == 'W/"1"'
    assert response.headers["Location"] == f"/api/v1/work/{work_id}"

    audit = scoped_session.execute(
        text(
            "SELECT action, actor, authorization_context FROM audit_entry "
            "WHERE resource_id = :id"
        ),
        {"id": work_id},
    ).mappings().all()
    assert len(audit) == 1
    assert audit[0]["action"] == "create"
    assert audit[0]["authorization_context"]["role"] == "member"

    events = scoped_session.execute(
        text("SELECT type, payload FROM outbox WHERE aggregate_id = :id"), {"id": work_id}
    ).mappings().all()
    assert [e["type"] for e in events] == ["WorkCreated"]
    assert events[0]["payload"]["partition"] == "non_project"


def test_a_refused_mutation_leaves_no_trace(
    api: TestClient, as_member: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """The rollback reaches the audit entry too, or audit records things that never happened."""
    created = api.post("/api/v1/work", json={"title": "Owned"}, headers=as_member).json()
    before = scoped_session.execute(text("SELECT count(*) FROM audit_entry")).scalar_one()

    # `todo -> done` is not an edge (BR-W-03).
    response = api.post(
        f"/api/v1/work/{created['id']}/status",
        json={"target": "done"},
        headers={**as_member, "If-Match": 'W/"1"'},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM)
    assert response.json()["type"] == "urn:workos:error:rule-violation"
    assert response.json()["rule"] == "BR-W-03"

    scoped_session.rollback()
    after = scoped_session.execute(text("SELECT count(*) FROM audit_entry")).scalar_one()
    assert after == before


# --------------------------------------------------------------------------- authentication


def test_a_request_without_a_token_is_refused(api: TestClient, work_org: WorkOrg) -> None:
    response = api.get(
        "/api/v1/work", headers={"X-Organization-Id": str(work_org.org_id)}
    )
    assert response.status_code == 401
    assert response.json()["type"] == "urn:workos:error:authentication-required"
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "flaw",
    ["expired", "wrong_signature", "wrong_audience", "wrong_issuer"],
    ids=["expired", "forged signature", "wrong audience", "wrong issuer"],
)
def test_four_ways_a_token_can_be_unacceptable(
    api: TestClient, realm: Realm, work_org: WorkOrg, flaw: str
) -> None:
    """Test 6. Four separate failures, one indistinguishable answer.

    Each is a different bug to us and the same fact to the caller. Telling an unauthenticated client
    which of the four it got wrong would let it tune its way towards a token that works.
    """
    if flaw == "expired":
        token = realm.token("someone", expires_in=-60)
    elif flaw == "wrong_signature":
        impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = realm.token("someone", key=impostor)
    elif flaw == "wrong_audience":
        token = realm.token("someone", audience="some-other-api")
    else:
        token = realm.token("someone", issuer="https://evil.test/realms/workos")

    response = api.get(
        "/api/v1/work",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": str(work_org.org_id),
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "the bearer token was not accepted"


def test_a_token_signed_by_an_unknown_key_is_refused(
    api: TestClient, realm: Realm, work_org: WorkOrg
) -> None:
    """An unknown `kid` forces one JWKS refetch, and is then refused rather than retried forever."""
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = realm.token("someone", key=impostor, kid="not-in-the-jwks")
    response = api.get(
        "/api/v1/work",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": str(work_org.org_id),
        },
    )
    assert response.status_code == 401


def test_a_token_that_is_not_a_jwt_at_all(
    api: TestClient, work_org: WorkOrg
) -> None:
    response = api.get(
        "/api/v1/work",
        headers={
            "Authorization": "Bearer not-a-token",
            "X-Organization-Id": str(work_org.org_id),
        },
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- org context


def test_the_organization_header_is_required(
    api: TestClient, realm: Realm, work_org: WorkOrg, scoped_session: Session
) -> None:
    """Test 1. Never inferred, not even when the caller belongs to exactly one organization.

    Inference of this kind is correct until the first person joins a second organization, and then
    it silently acts on the wrong one.
    """
    token = realm.token(subject_of(scoped_session, work_org.admin))
    response = api.get("/api/v1/work", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert response.json()["type"] == "urn:workos:error:organization-context-required"


def test_a_malformed_organization_header_is_a_bad_request(
    api: TestClient, realm: Realm, work_org: WorkOrg, scoped_session: Session
) -> None:
    token = realm.token(subject_of(scoped_session, work_org.admin))
    response = api.get(
        "/api/v1/work",
        headers={"Authorization": f"Bearer {token}", "X-Organization-Id": "not-a-uuid"},
    )
    assert response.status_code == 400
    assert response.json()["type"] == "urn:workos:error:organization-context-required"


@pytest.mark.parametrize("existing", [False, True], ids=["absent org", "real org"])
def test_an_organization_the_caller_is_not_in_is_not_found(
    api: TestClient,
    realm: Realm,
    work_org: WorkOrg,
    scoped_session: Session,
    two_orgs: tuple[uuid.UUID, uuid.UUID],
    existing: bool,
) -> None:
    """404 rather than 403, and the same 404 either way.

    Whether a given organization exists is itself tenant information. If "not a member" and "no such
    organization" produced different answers, anyone with a token could enumerate the tenants
    (contract §5).
    """
    token = realm.token(subject_of(scoped_session, work_org.admin))
    org_id = two_orgs[0] if existing else uuid.uuid4()
    response = api.get(
        "/api/v1/work",
        headers={"Authorization": f"Bearer {token}", "X-Organization-Id": str(org_id)},
    )
    assert response.status_code == 404
    assert response.json() == {
        "type": "urn:workos:error:not-found",
        "title": "Not found",
        "status": 404,
        "detail": "No such resource is visible in this organization.",
    }


def test_a_member_holding_no_role_is_permitted_nothing(
    api: TestClient, realm: Realm, work_org: WorkOrg, scoped_session: Session
) -> None:
    """Known, and authorized for nothing. 403, because 404 would be a lie about membership."""
    token = realm.token(subject_of(scoped_session, work_org.outsider))
    response = api.get(
        "/api/v1/work",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": str(work_org.org_id),
        },
    )
    assert response.status_code == 403
    assert response.json()["type"] == "urn:workos:error:forbidden"


# --------------------------------------------------------------------------- cross-tenant


def test_another_organizations_work_is_invisible_on_every_endpoint(
    api: TestClient,
    realm: Realm,
    work_org: WorkOrg,
    roles: None,
    as_admin: dict[str, str],
    app_session_factory: sessionmaker[Session],
) -> None:
    """Test 2. Every endpoint, and a body that gives nothing away."""
    other_org = uuid7()
    secret_title = "Project Ozymandias reorganisation"
    session = app_session_factory()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(other_org)}
    )
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Other', :slug)"),
        {"id": other_org, "slug": f"other-{uuid.uuid4().hex[:10]}"},
    )
    foreign_work = uuid7()
    session.execute(
        text(
            "INSERT INTO work (id, org_id, title, status) "
            "VALUES (:id, :org, :title, 'todo')"
        ),
        {"id": foreign_work, "org": other_org, "title": secret_title},
    )
    foreign_assignment = uuid7()
    session.commit()
    session.close()

    calls = [
        ("GET", f"/api/v1/work/{foreign_work}", None, {}),
        ("PATCH", f"/api/v1/work/{foreign_work}", {"title": "mine"}, {"If-Match": 'W/"1"'}),
        (
            "POST",
            f"/api/v1/work/{foreign_work}/status",
            {"target": "in_progress"},
            {"If-Match": 'W/"1"'},
        ),
        (
            "POST",
            f"/api/v1/work/{foreign_work}/assignments",
            {"person_id": str(work_org.member)},
            {},
        ),
        (
            "DELETE",
            f"/api/v1/work/{foreign_work}/assignments/{foreign_assignment}",
            None,
            {"If-Match": 'W/"1"'},
        ),
    ]
    for method, path, payload, extra in calls:
        response = api.request(
            method, path, json=payload, headers={**as_admin, **extra}
        )
        assert response.status_code == 404, f"{method} {path} -> {response.status_code}"
        assert secret_title not in response.text
        assert str(other_org) not in response.text
        assert response.json()["detail"] == "No such resource is visible in this organization."

    listed = api.get("/api/v1/work", headers=as_admin).json()
    assert all(item["id"] != str(foreign_work) for item in listed["items"])
    assert secret_title not in api.get("/api/v1/work", headers=as_admin).text


# --------------------------------------------------------------------------- etag


def test_etag_round_trip(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """Test 4."""
    created = api.post("/api/v1/work", json={"title": "Draft"}, headers=as_member).json()
    work_id = created["id"]

    read = api.get(f"/api/v1/work/{work_id}", headers=as_member)
    tag = read.headers["ETag"]
    assert tag == 'W/"1"'

    updated = api.patch(
        f"/api/v1/work/{work_id}",
        json={"title": "Second draft"},
        headers={**as_member, "If-Match": tag},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Second draft"
    assert updated.headers["ETag"] == 'W/"2"'

    stale = api.patch(
        f"/api/v1/work/{work_id}",
        json={"title": "Third draft"},
        headers={**as_member, "If-Match": tag},
    )
    assert stale.status_code == 412
    assert stale.json()["type"] == "urn:workos:error:stale-version"

    missing = api.patch(
        f"/api/v1/work/{work_id}", json={"title": "Fourth"}, headers=as_member
    )
    assert missing.status_code == 428
    assert missing.json()["type"] == "urn:workos:error:precondition-required"

    unchanged = api.get(f"/api/v1/work/{work_id}", headers=as_member).json()
    assert unchanged["title"] == "Second draft"


def test_an_if_match_that_is_not_a_tag_we_issued_fails_the_precondition(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    """RFC 9110: a failed `If-Match` is 412 whatever the reason it failed."""
    created = api.post("/api/v1/work", json={"title": "Draft"}, headers=as_member).json()
    response = api.patch(
        f"/api/v1/work/{created['id']}",
        json={"title": "nope"},
        headers={**as_member, "If-Match": "banana"},
    )
    assert response.status_code == 412


# --------------------------------------------------------------------------- pagination


def test_pagination_returns_every_row_once(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """Test 5. Keyset paging over more rows than a page holds."""
    titles = {f"Item {index:02d}" for index in range(12)}
    for title in sorted(titles):
        assert (
            api.post("/api/v1/work", json={"title": title}, headers=as_admin).status_code
            == 201
        )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # generous bound; the loop must terminate well inside it
        query = "?limit=5" + (f"&cursor={cursor}" if cursor else "")
        page = api.get(f"/api/v1/work{query}", headers=as_admin).json()
        seen.extend(item["title"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert cursor is None, "pagination did not terminate"
    assert len(seen) == len(set(seen)), "a row was returned on two pages"
    assert titles <= set(seen)
    # No count is exposed, because a count is an inference channel (contract §5).
    assert "total" not in api.get("/api/v1/work", headers=as_admin).json()


def test_an_invented_cursor_is_a_bad_request_not_a_crash(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    response = api.get("/api/v1/work?cursor=not-a-real-cursor", headers=as_admin)
    assert response.status_code == 400
    assert response.json()["type"] == "urn:workos:error:invalid-cursor"


def test_an_oversized_limit_is_clamped_rather_than_refused(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    assert api.get("/api/v1/work?limit=5000", headers=as_admin).status_code == 200


# --------------------------------------------------------------------------- filters


def test_the_partition_filter_splits_all_work_without_losing_any(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    scoped_session: Session,
    work_org: WorkOrg,
) -> None:
    """BR-RPT-01 and BR-RPT-03: both partitions, and unfiltered means both."""
    project_id = uuid7()
    scoped_session.execute(
        text(
            "INSERT INTO project (id, org_id, name, owning_team_id, status) "
            "VALUES (:id, :org, 'Migration', :team, 'active')"
        ),
        {"id": project_id, "org": work_org.org_id, "team": work_org.team_id},
    )
    scoped_session.commit()

    api.post("/api/v1/work", json={"title": "In a project", "project_id": str(project_id)},
             headers=as_admin)
    api.post("/api/v1/work", json={"title": "Unparented"}, headers=as_admin)

    everything = api.get("/api/v1/work", headers=as_admin).json()["items"]
    project_only = api.get("/api/v1/work?partition=project", headers=as_admin).json()["items"]
    loose_only = api.get(
        "/api/v1/work?partition=non_project", headers=as_admin
    ).json()["items"]

    assert {i["title"] for i in project_only} == {"In a project"}
    assert {i["title"] for i in loose_only} == {"Unparented"}
    assert len(everything) == len(project_only) + len(loose_only)


def test_filters_narrow_without_widening(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    api.post("/api/v1/work", json={"title": "Soon", "due_date": "2026-01-01"}, headers=as_admin)
    api.post("/api/v1/work", json={"title": "Later", "due_date": "2027-01-01"}, headers=as_admin)

    due = api.get("/api/v1/work?due_before=2026-06-01", headers=as_admin).json()["items"]
    assert {i["title"] for i in due} == {"Soon"}

    todo = api.get("/api/v1/work?status=todo", headers=as_admin).json()["items"]
    assert len(todo) >= 2
    assert api.get("/api/v1/work?status=done", headers=as_admin).json()["items"] == []


def test_an_unknown_filter_value_is_a_validation_error(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    response = api.get("/api/v1/work?status=nonsense", headers=as_admin)
    assert response.status_code == 422
    assert response.json()["type"] == "urn:workos:error:validation-failed"


# --------------------------------------------------------------------------- assignment


def test_the_capture_flow_end_to_end(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """D1. Capture, read back, claim, work it, finish it — as a plain member, start to end.

    This is the flow principle P4 exists for. If any step here needs a team lead, the product's
    central claim about data-entry cost is false.
    """
    created = api.post(
        "/api/v1/work", json={"title": "Check the IOC API"}, headers=as_member
    )
    assert created.status_code == 201
    work_id = created.json()["id"]

    assert api.get(f"/api/v1/work/{work_id}", headers=as_member).status_code == 200

    claimed = api.post(
        f"/api/v1/work/{work_id}/assignments",
        json={"person_id": str(work_org.member), "role": "OWNER"},
        headers=as_member,
    )
    assert claimed.status_code == 201, claimed.text

    started = api.post(
        f"/api/v1/work/{work_id}/status",
        json={"target": "in_progress"},
        headers={**as_member, "If-Match": 'W/"1"'},
    )
    assert started.status_code == 200
    done = api.post(
        f"/api/v1/work/{work_id}/status",
        json={"target": "done"},
        headers={**as_member, "If-Match": started.headers["ETag"]},
    )
    assert done.status_code == 200
    assert done.json()["completed_at"] is not None


def test_a_creator_cannot_hand_their_work_to_somebody_else(
    api: TestClient, as_member: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The other half of D1, and the half that keeps it from being a staffing permission.

    Taking work you wrote down is capture. Directing another person's time is not, and writing a
    note about them does not make it so (ADR-0032).
    """
    created = api.post("/api/v1/work", json={"title": "Someone should"}, headers=as_member)
    work_id = created.json()["id"]

    response = api.post(
        f"/api/v1/work/{work_id}/assignments",
        json={"person_id": str(work_org.outsider), "role": "CONTRIBUTOR"},
        headers=as_member,
    )
    assert response.status_code == 403
    assert response.json()["type"] == "urn:workos:error:forbidden"


def test_ending_an_assignment_is_an_update_not_a_deletion(
    api: TestClient,
    as_admin: dict[str, str],
    as_member: dict[str, str],
    roles: None,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-W-14 seen through a DELETE. The verb is the client's; the row survives."""
    work_id = api.post("/api/v1/work", json={"title": "Shared"}, headers=as_admin).json()["id"]
    assignment = api.post(
        f"/api/v1/work/{work_id}/assignments",
        json={"person_id": str(work_org.member)},
        headers=as_admin,
    ).json()

    ended = api.request(
        "DELETE",
        f"/api/v1/work/{work_id}/assignments/{assignment['id']}",
        headers={**as_member, "If-Match": f'W/"{assignment["version"]}"'},
    )
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert ended.json()["ended_at"] is not None

    scoped_session.rollback()
    assert (
        scoped_session.execute(
            text("SELECT count(*) FROM work_assignment WHERE id = :id"),
            {"id": assignment["id"]},
        ).scalar_one()
        == 1
    )


def test_ending_an_assignment_requires_if_match(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    work_id = api.post("/api/v1/work", json={"title": "Shared"}, headers=as_admin).json()["id"]
    assignment = api.post(
        f"/api/v1/work/{work_id}/assignments",
        json={"person_id": str(work_org.member)},
        headers=as_admin,
    ).json()
    response = api.request(
        "DELETE",
        f"/api/v1/work/{work_id}/assignments/{assignment['id']}",
        headers=as_admin,
    )
    assert response.status_code == 428


# --------------------------------------------------------------------------- partial update


def test_an_omitted_field_is_left_alone(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    created = api.post(
        "/api/v1/work",
        json={"title": "Draft", "description": "context", "due_date": "2026-05-01"},
        headers=as_admin,
    ).json()
    updated = api.patch(
        f"/api/v1/work/{created['id']}",
        json={"title": "Renamed"},
        headers={**as_admin, "If-Match": 'W/"1"'},
    ).json()
    assert updated["description"] == "context"
    assert updated["due_date"] == "2026-05-01"

    cleared = api.patch(
        f"/api/v1/work/{created['id']}",
        json={"due_date": None},
        headers={**as_admin, "If-Match": 'W/"2"'},
    ).json()
    assert cleared["due_date"] is None
    assert cleared["description"] == "context"


def test_an_unknown_field_is_rejected_rather_than_ignored(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """A typo that is silently dropped is a change the caller believes they made."""
    response = api.post(
        "/api/v1/work",
        json={"title": "Draft", "assignee_person_id": str(uuid.uuid4())},
        headers=as_admin,
    )
    assert response.status_code == 422
    assert response.json()["type"] == "urn:workos:error:validation-failed"


def test_dates_and_versions_round_trip_as_written(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    created = api.post(
        "/api/v1/work", json={"title": "Dated", "due_date": "2026-12-31"}, headers=as_admin
    ).json()
    assert created["due_date"] == "2026-12-31"
    assert created["version"] == 1
    assert dt.date.fromisoformat(created["due_date"]) == dt.date(2026, 12, 31)
