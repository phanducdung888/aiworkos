"""U+0000 in a request string.

Found by Hypothesis during Checkpoint 4b's close-out, and worth writing down precisely because of
how it hid. The defect is deterministic — a NUL byte in any text field has always produced a 500 —
but its discovery was not, because Hypothesis only sometimes generates one. Six full runs of the
suite passed; the seventh did not. A bug that fails one run in seven is worse than one that fails
every time: it gets attributed to the infrastructure and re-run away.

The mechanism: PostgreSQL cannot store U+0000 in `text`, and psycopg refuses to encode the parameter
before the statement is even sent, raising `DataError`. `DataError` and `IntegrityError` are
siblings under `DatabaseError`, so the handler added in 4b for foreign-key misses never saw it and
the request fell through to the generic 500.

These tests are explicit rather than generated, so the regression cannot go back to being
occasional.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration

NUL = "\x00"

#: Exactly the shape Hypothesis produced on the failing run: a NUL inside an otherwise ordinary
#: name, on POST /api/v1/projects.
HYPOTHESIS_DISCOVERED_NAME = "W\x9bv\x91\x87\x00"


def table_counts(session: Session) -> tuple[int, int, int, int, int]:
    """Everything a refused request must not have touched."""
    session.rollback()
    return tuple(  # type: ignore[return-value]
        int(
            session.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()  # noqa: S608
        )
        for name in ("work", "project", "milestone", "dependency", "audit_entry")
    )


def outbox_count(session: Session) -> int:
    session.rollback()
    return int(session.execute(text("SELECT count(*) FROM outbox")).scalar_one())


def assert_rejected(response: object, field: str) -> None:
    """422, the existing problem+json contract, and the offending field named."""
    assert response.status_code == 422, response.text  # type: ignore[attr-defined]
    assert response.headers["content-type"].startswith(  # type: ignore[attr-defined]
        "application/problem+json"
    )
    body = response.json()  # type: ignore[attr-defined]
    assert body["type"] == "urn:workos:error:validation-failed"
    assert body["status"] == 422
    locations = [error["loc"] for error in body["errors"]]
    assert any(field in loc for loc in locations), (
        f"the response does not say which field was wrong: {locations}"
    )
    # The rejected value must not come back. Echoing it would put an unstorable byte into the
    # caller's logs and ours, which is how one bad request becomes several.
    assert NUL not in response.text  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- the exact regression


def test_the_request_hypothesis_found_is_refused_without_touching_the_database(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The failing case from the close-out run, reproduced exactly."""
    before, before_events = table_counts(scoped_session), outbox_count(scoped_session)

    response = api.post(
        "/api/v1/projects",
        json={
            "name": HYPOTHESIS_DISCOVERED_NAME,
            "owning_team_id": str(work_org.team_id),
            "description": "Precondition failed",
            "objective": "work",
        },
        headers=as_admin,
    )
    assert_rejected(response, "name")
    assert table_counts(scoped_session) == before
    assert outbox_count(scoped_session) == before_events


# --------------------------------------------------------------------------- every text field


@pytest.mark.parametrize(
    ("label", "method", "path", "payload", "field"),
    [
        ("work title", "POST", "/api/v1/work", {"title": f"bad{NUL}title"}, "title"),
        (
            "work description",
            "POST",
            "/api/v1/work",
            {"title": "fine", "description": f"a{NUL}b"},
            "description",
        ),
        (
            "project name",
            "POST",
            "/api/v1/projects",
            {"name": f"p{NUL}q"},
            "name",
        ),
        (
            "project objective",
            "POST",
            "/api/v1/projects",
            {"name": "fine", "objective": f"o{NUL}bj"},
            "objective",
        ),
        (
            "dependency rationale",
            "POST",
            "/api/v1/dependencies",
            {
                "blocker_type": "work",
                "blocker_id": "00000000-0000-0000-0000-000000000001",
                "blocked_type": "work",
                "blocked_id": "00000000-0000-0000-0000-000000000002",
                "rationale": f"because{NUL}",
            },
            "rationale",
        ),
    ],
)
def test_a_nul_byte_is_refused_on_every_text_field(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    scoped_session: Session,
    label: str,
    method: str,
    path: str,
    payload: dict[str, object],
    field: str,
) -> None:
    before, before_events = table_counts(scoped_session), outbox_count(scoped_session)
    response = api.request(method, path, json=payload, headers=as_admin)
    assert_rejected(response, field)
    assert table_counts(scoped_session) == before, f"{label} wrote a row"
    assert outbox_count(scoped_session) == before_events, f"{label} emitted an event"


def test_a_nul_byte_is_refused_in_a_milestone_field(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    project = api.post(
        "/api/v1/projects",
        json={"name": "Host", "owning_team_id": str(work_org.team_id)},
        headers=as_admin,
    ).json()
    before, before_events = table_counts(scoped_session), outbox_count(scoped_session)

    response = api.post(
        f"/api/v1/projects/{project['id']}/milestones",
        json={"name": "Cutover", "acceptance_criteria": f"signed{NUL}off"},
        headers=as_admin,
    )
    assert_rejected(response, "acceptance_criteria")
    assert table_counts(scoped_session) == before
    assert outbox_count(scoped_session) == before_events


def test_a_nul_byte_is_refused_in_a_patch_payload(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """A partial update reaches the same columns, so it needs the same answer."""
    work = api.post("/api/v1/work", json={"title": "Original"}, headers=as_admin).json()
    before, before_events = table_counts(scoped_session), outbox_count(scoped_session)

    response = api.patch(
        f"/api/v1/work/{work['id']}",
        json={"title": f"renamed{NUL}"},
        headers={**as_admin, "If-Match": 'W/"1"'},
    )
    assert_rejected(response, "title")
    assert table_counts(scoped_session) == before
    assert outbox_count(scoped_session) == before_events

    scoped_session.rollback()
    unchanged = scoped_session.execute(
        text("SELECT title, version FROM work WHERE id = :id"), {"id": uuid.UUID(work["id"])}
    ).one()
    assert unchanged.title == "Original"
    assert unchanged.version == 1


def test_a_nul_byte_is_refused_in_a_status_change_reason(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    work = api.post("/api/v1/work", json={"title": "Blockable"}, headers=as_admin).json()
    started = api.post(
        f"/api/v1/work/{work['id']}/status",
        json={"target": "in_progress"},
        headers={**as_admin, "If-Match": 'W/"1"'},
    )
    before = table_counts(scoped_session)

    response = api.post(
        f"/api/v1/work/{work['id']}/status",
        json={"target": "blocked", "blocked_reason": f"waiting{NUL}on legal"},
        headers={**as_admin, "If-Match": started.headers["ETag"]},
    )
    assert_rejected(response, "blocked_reason")
    assert table_counts(scoped_session) == before


def test_a_nul_byte_in_the_idempotency_key_header_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """The second route to the same column, and the one a body-only fix would have missed.

    `Idempotency-Key` is persisted in `idempotency_key.key`, a text column like any other. It is not
    a Pydantic body field, so validating the schemas alone left it producing the same 500.
    """
    before = table_counts(scoped_session)
    response = api.post(
        "/api/v1/work",
        json={"title": "Fine"},
        headers={**as_admin, "Idempotency-Key": f"key{NUL}probe"},
    )
    assert response.status_code == 422
    assert response.json()["type"] == "urn:workos:error:validation-failed"
    assert table_counts(scoped_session) == before


# --------------------------------------------------------------------------- property


@settings(
    max_examples=25,
    deadline=None,
    # Derandomised on purpose. This bug was found by a randomised run and missed by six others;
    # a fixed seed means the check either holds for every run or fails for every run, which is the
    # difference between a test and a lottery.
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    phases=[Phase.explicit, Phase.generate],
)
@given(
    prefix=st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=12),
    suffix=st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=12),
)
def test_a_nul_anywhere_in_a_string_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None, prefix: str, suffix: str
) -> None:
    """Position does not matter, and neither does what surrounds it.

    The enumerated cases above put the NUL in the middle of an ASCII word. This says the same thing
    about a NUL at the start, at the end, alone, or between arbitrary Unicode — the shapes a
    hand-written list quietly omits.
    """
    response = api.post(
        "/api/v1/work", json={"title": f"{prefix}{NUL}{suffix}"}, headers=as_admin
    )
    assert response.status_code == 422, response.text[:200]
    assert response.json()["type"] == "urn:workos:error:validation-failed"


# --------------------------------------------------------------------------- what stays valid


@pytest.mark.parametrize(
    ("label", "title", "description"),
    [
        ("Vietnamese", "Kiểm tra IOC API trước thứ Sáu", "Đã trao đổi với đội hạ tầng"),
        ("accents", "Café façade naïve", "résumé"),
        ("emoji", "Ship it 🚀🎉", "status: 👍"),
        ("newline and tab", "Multi line", "first\nsecond\tthird"),
        ("punctuation", "Q3 — \"scope\": 50% (final); ok?", "a/b\\c<d>e&f'g"),
        ("long text", "t", "x" * 5000),
        ("other control chars", "bell\x07 and vertical tab\x0b", "form feed\x0c"),
    ],
)
def test_ordinary_text_is_still_accepted(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    label: str,
    title: str,
    description: str,
) -> None:
    """The rule is U+0000 and nothing else.

    `other control chars` is in this list on purpose. PostgreSQL stores them, no business rule
    forbids them, and a blacklist that quietly grew to cover them would be a rule invented in the
    validation layer — which is the one place least able to say why.
    """
    response = api.post(
        "/api/v1/work",
        json={"title": title, "description": description},
        headers=as_admin,
    )
    assert response.status_code == 201, f"{label} was rejected: {response.text[:200]}"
    assert response.json()["title"] == title
    assert response.json()["description"] == description


def test_valid_text_round_trips_through_the_database_unchanged(
    api: TestClient, as_admin: dict[str, str], roles: None, scoped_session: Session
) -> None:
    """Accepted at the edge is not the same as stored intact."""
    title = "Kiểm tra 🚀 — dòng 1\ndòng 2\tcuối"
    created = api.post("/api/v1/work", json={"title": title}, headers=as_admin).json()
    scoped_session.rollback()
    stored = scoped_session.execute(
        text("SELECT title FROM work WHERE id = :id"), {"id": uuid.UUID(created["id"])}
    ).scalar_one()
    assert stored == title
    assert api.get(f"/api/v1/work/{created['id']}", headers=as_admin).json()["title"] == title
