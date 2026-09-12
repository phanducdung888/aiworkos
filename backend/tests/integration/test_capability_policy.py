"""The organization's autonomy policy over HTTP (ADR-0047, BR-AI-30).

Most of this file is about the default. A permission table's dangerous failure is not the row that
is wrong — it is the row that is missing being read as "no restriction", and an AI writing into an
organization nobody decided to switch on. So the tests assert denial repeatedly and from several
directions, and there is exactly one test that a grant works at all.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.conftest import Realm, WorkOrg, auth, grant, subject_of

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
PROMISE = "Thanks for the call. I will send the revised quote on Friday."


def set_policy(api: TestClient, headers: dict[str, str], **over: object) -> object:
    payload: dict[str, object] = {
        "capability": "extract",
        "entity_type": "work",
        "action": "create",
        "mode": "level_1_propose",
    }
    payload.update(over)
    return api.put("/api/v1/agent-policy", json=payload, headers=headers)


def an_event(
    api: TestClient, headers: dict[str, str], speaker: uuid.UUID | None = None
) -> dict:
    """An Event, optionally naming who spoke.

    The speaker matters since CP11: an agent may only attribute a commitment to somebody the Event
    already resolved (BR-AI-34, ADR-0052). Without one, a commitment span degrades to Work — which
    is correct, and means a test about *commitment* policy has to supply a participant or it is
    quietly testing the Work path instead.
    """
    payload: dict[str, object] = {
        "type": "EXTERNAL_MESSAGE",
        "occurred_at": NOW.isoformat(),
        "body_text": PROMISE,
        "source_system": "openclaw.whatsapp",
        "source_ref": f"m-{uuid.uuid4().hex[:10]}",
    }
    if speaker is not None:
        payload["participants"] = [{"role": "speaker", "person_id": str(speaker)}]
    response = api.post("/api/v1/events", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- deny by default


def test_a_new_organization_grants_nothing(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    listed = api.get("/api/v1/agent-policy", headers=as_admin)
    assert listed.status_code == 200
    assert listed.json()["items"] == [], "a new organization has decided nothing"


def test_an_agent_proposes_nothing_without_a_policy(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The failure mode this design refuses to have.

    The agent runs, the interaction is recorded, and nothing is proposed — because nobody decided
    it could. A missing row is denial, not absence of restriction.
    """
    result = api.post(
        f"/api/v1/events/{an_event(api, as_admin)['id']}/analyze", headers=as_admin
    )
    assert result.status_code == 201, result.text
    assert result.json()["proposal_ids"] == []

    scoped_session.rollback()
    assert scoped_session.execute(
        text("SELECT count(*) FROM proposal WHERE org_id = :org"),
        {"org": work_org.org_id},
    ).scalar_one() == 0

    # The run is still recorded, and the refusal with it. A denial is the row worth reading.
    interaction = api.get(
        f"/api/v1/ai-interactions/{result.json()['ai_interaction_id']}", headers=as_admin
    ).json()
    denied = [c for c in interaction["tool_calls"] if c["authorization_result"] == "denied"]
    assert denied, "the refusal must be recorded, not merely produce nothing"


def test_a_policy_for_one_entity_does_not_enable_another(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """Commitment is enabled; Work is not. The span is a promise by a named speaker, so it stays a
    commitment — and the refusal is the policy, not the mapping."""
    assert set_policy(api, as_admin, entity_type="commitment").status_code == 200
    event = an_event(api, as_admin, speaker=work_org.member)
    result = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin).json()
    assert result["proposal_ids"], "commitment is enabled and the speaker is resolved"

    # Now the other way round: only Work enabled, and a span that resolves to a commitment.
    assert set_policy(api, as_admin, entity_type="commitment", mode="off").status_code == 200
    assert set_policy(api, as_admin, entity_type="work").status_code == 200
    second = api.post(
        f"/api/v1/events/{an_event(api, as_admin, speaker=work_org.member)['id']}/analyze",
        headers=as_admin,
    ).json()
    assert second["proposal_ids"] == []


def test_an_explicit_off_denies(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """A row saying `off` and no row at all have the same effect, deliberately.

    The difference is the audit entry: an explicit `off` records that somebody decided, rather than
    that nobody looked.
    """
    assert set_policy(api, as_admin, entity_type="commitment", mode="off").status_code == 200
    result = api.post(
        f"/api/v1/events/{an_event(api, as_admin)['id']}/analyze", headers=as_admin
    ).json()
    assert result["proposal_ids"] == []


def test_a_granted_policy_lets_the_agent_propose(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
) -> None:
    """The control. Without it, a policy that denied everything would pass every test above."""
    assert set_policy(api, as_admin, entity_type="commitment").status_code == 200
    event = an_event(api, as_admin, speaker=work_org.member)
    result = api.post(f"/api/v1/events/{event['id']}/analyze", headers=as_admin).json()
    assert result["proposal_ids"], "an enabled capability should produce a proposal"


def test_an_unattributable_promise_becomes_work_not_a_commitment(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """BR-AI-34, ADR-0052. The observation survives; the unevidenced attribution does not.

    The text says somebody will send a quote, and no participant is resolved. Claiming *who*
    promised would be a guess — and the runtime used to fill it with whoever ran the analysis,
    which is worse than guessing because it is systematically wrong in a plausible direction.

    Work needs no owner (BR-W-07), so the useful half is kept and the unevidenced half is dropped.
    """
    assert set_policy(api, as_admin, entity_type="work").status_code == 200
    result = api.post(
        f"/api/v1/events/{an_event(api, as_admin)['id']}/analyze", headers=as_admin
    ).json()
    assert result["proposal_ids"], "the observation is still actionable"

    proposal = api.get(
        f"/api/v1/proposals/{result['proposal_ids'][0]}", headers=as_admin
    ).json()
    assert proposal["target_type"] == "work"
    assert "committed_by_person_id" not in proposal["action"]["arguments"]


# --------------------------------------------------------------------------- authorization


def test_only_an_admin_may_set_the_policy(
    api: TestClient, as_member: dict[str, str], roles: None
) -> None:
    assert set_policy(api, as_member).status_code == 403


def test_everyone_may_read_it(
    api: TestClient, realm: Realm, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """Somebody reviewing an AI-raised Proposal is entitled to know what the AI could do."""
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "viewer")
    viewer = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    assert api.get("/api/v1/agent-policy", headers=viewer).status_code == 200


def test_level_three_is_not_accepted(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """BR-AI-31. Not disabled — not representable."""
    assert set_policy(api, as_admin, mode="level_3_autonomous").status_code == 422


def test_setting_a_cell_twice_is_one_row(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """PUT, not POST: a cell is set, not created, so "what is the policy" has one answer."""
    first = set_policy(api, as_admin, reason="trying it out")
    second = set_policy(api, as_admin, mode="off", reason="too noisy")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["mode"] == "off"
    assert second.json()["version"] > first.json()["version"]

    listed = api.get("/api/v1/agent-policy", headers=as_admin).json()["items"]
    assert len([row for row in listed if row["entity_type"] == "work"]) == 1


def test_a_policy_change_is_audited(
    api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-AI-32's promotion process needs something to point at."""
    row = set_policy(api, as_admin, reason="approved at the safety review").json()
    scoped_session.rollback()
    entry = scoped_session.execute(
        text(
            "SELECT action, resource_type, actor, after_state FROM audit_entry "
            "WHERE resource_id = :id"
        ),
        {"id": uuid.UUID(row["id"])},
    ).mappings().one()
    assert entry["resource_type"] == "agent_capability_policy"
    assert entry["actor"]["person_id"] == str(work_org.admin)
    assert entry["after_state"]["reason"] == "approved at the safety review"


def test_another_organizations_policy_is_invisible(
    api: TestClient, as_admin: dict[str, str], roles: None, app_session_factory
) -> None:
    from app.platform.ids import uuid7

    other_org = uuid7()
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
            "INSERT INTO agent_capability_policy (id, org_id, capability, entity_type, action, "
            "mode) VALUES (:id, :org, 'extract', 'work', 'create', "
            "'level_2_approved_execution')"
        ),
        {"id": uuid7(), "org": other_org},
    )
    session.commit()
    session.close()

    listed = api.get("/api/v1/agent-policy", headers=as_admin).json()["items"]
    assert listed == [], "another organization's policy must not be visible, let alone effective"
