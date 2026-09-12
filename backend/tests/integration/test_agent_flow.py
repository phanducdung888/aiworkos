"""Event → AIInteraction → Evidence → Proposal, and the line it stops at.

The most important assertion in this file is a negative one: after a run, no Work and no Commitment
exists. Level 1 observes and proposes (PQ-3); everything else waits for a person. A test suite that
only checked what the agent produced would pass just as happily against a runtime that had also
quietly created the entities.

Everything runs against the deterministic fake provider — no network, no credentials, no skipped
tests in the environment that should run them.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.conftest import Realm, WorkOrg, auth, execute_approval, grant, subject_of

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)
PROMISE = "Thanks for the call. I will send the revised quote on Friday."


def an_event(api: TestClient, headers: dict[str, str], body: str = PROMISE, **over: object) -> dict:
    payload: dict[str, object] = {
        "type": "EXTERNAL_MESSAGE",
        "occurred_at": NOW.isoformat(),
        "body_text": body,
        "source_system": "openclaw.whatsapp",
        "source_ref": f"m-{uuid.uuid4().hex[:10]}",
    }
    payload.update(over)
    response = api.post("/api/v1/events", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def analyze(api: TestClient, headers: dict[str, str], event: dict) -> dict:
    response = api.post(f"/api/v1/events/{event['id']}/analyze", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def counts(session: Session, org_id: uuid.UUID) -> dict[str, int]:
    session.rollback()
    return {
        table: session.execute(
            text(f"SELECT count(*) FROM {table} WHERE org_id = :org"), {"org": org_id}
        ).scalar_one()
        for table in ("work", "commitment", "proposal", "evidence", "ai_interaction")
    }


# --------------------------------------------------------------------------- the flow


def test_a_run_produces_evidence_and_proposals(
    api: TestClient, as_admin: dict[str, str], roles: None, agent_enabled: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    event = an_event(api, as_admin)
    result = analyze(api, as_admin, event)

    assert result["proposal_ids"], "the fake provider should find the promise in this text"
    assert len(result["evidence_ids"]) == len(result["proposal_ids"])

    proposal = api.get(
        f"/api/v1/proposals/{result['proposal_ids'][0]}", headers=as_admin
    ).json()
    assert proposal["status"] == "pending"
    assert proposal["source_event_id"] == event["id"]
    assert proposal["evidence_ids"]


def test_a_run_executes_nothing(
    api: TestClient, as_admin: dict[str, str], roles: None, agent_enabled: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """Level 1. The negative assertion this whole checkpoint rests on."""
    before = counts(scoped_session, work_org.org_id)
    result = analyze(api, as_admin, an_event(api, as_admin))
    after = counts(scoped_session, work_org.org_id)

    assert result["proposal_ids"], "nothing was proposed, so this proves nothing"
    assert after["work"] == before["work"], "the agent created work without approval"
    assert after["commitment"] == before["commitment"], "the agent created a commitment"
    assert after["proposal"] > before["proposal"]
    assert after["ai_interaction"] == before["ai_interaction"] + 1


def test_the_citation_quotes_the_event_verbatim(
    api: TestClient, as_admin: dict[str, str], roles: None,
    worker_session_factory: sessionmaker[Session],
    agent_enabled: None,
) -> None:
    """BR-E-05, and the reason the provider returns spans rather than summaries.

    A model that paraphrased would produce Evidence the service refuses — which is the correct
    outcome, and is why this is checked against the Event rather than against the model's claim.
    """
    event = an_event(api, as_admin)
    result = analyze(api, as_admin, event)
    evidence = api.get(
        f"/api/v1/evidence/{result['evidence_ids'][0]}", headers=as_admin
    ).json()
    assert evidence["excerpt"] in event["body_text"]
    start = evidence["locator"]["char_start"]
    end = evidence["locator"]["char_end"]
    assert event["body_text"][start:end] == evidence["excerpt"]


def test_ai_output_carries_its_interaction(
    api: TestClient, as_admin: dict[str, str], roles: None, agent_enabled: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-AI-02, ADR-0043. Derived from the actor, so it cannot be forgotten or declined."""
    result = analyze(api, as_admin, an_event(api, as_admin))
    scoped_session.rollback()

    proposal = scoped_session.execute(
        text("SELECT ai_interaction_id FROM proposal WHERE id = :id"),
        {"id": uuid.UUID(result["proposal_ids"][0])},
    ).scalar_one()
    assert proposal == uuid.UUID(result["ai_interaction_id"])

    produced_by_type, produced_by_id = scoped_session.execute(
        text("SELECT produced_by_type, produced_by_id FROM evidence WHERE id = :id"),
        {"id": uuid.UUID(result["evidence_ids"][0])},
    ).one()
    assert produced_by_type == "ai_interaction"
    assert produced_by_id == uuid.UUID(result["ai_interaction_id"])


def test_the_chain_traverses_from_a_work_item_back_to_the_words(
    api: TestClient, as_admin: dict[str, str], roles: None, agent_enabled: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-PR-08, with the AI hop in it.

    Event → Evidence → AIInteraction → Proposal → ApprovalRecord → Work, resolved in one query.
    """
    event = an_event(api, as_admin)
    result = analyze(api, as_admin, event)
    proposal = api.get(
        f"/api/v1/proposals/{result['proposal_ids'][0]}", headers=as_admin
    ).json()

    record = api.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "approved"},
        headers={**as_admin, "If-Match": f'W/"{proposal["version"]}"'},
    )
    assert record.status_code == 201, record.text
    executed = execute_approval(api, as_admin, record.json()['id'], worker_session_factory)
    assert executed.execution_status == "executed", executed.body

    scoped_session.rollback()
    row = scoped_session.execute(
        text(
            """
            SELECT e.body_text, v.excerpt, i.agent_identity, i.provider, i.prompt_version
            FROM approval_record a
            JOIN proposal p ON p.id = a.proposal_id AND p.org_id = a.org_id
            JOIN ai_interaction i ON i.id = p.ai_interaction_id AND i.org_id = p.org_id
            JOIN proposal_evidence pe ON pe.proposal_id = p.id AND pe.org_id = p.org_id
            JOIN evidence v ON v.id = pe.evidence_id AND v.org_id = pe.org_id
            JOIN event e ON e.id = v.event_id AND e.org_id = v.org_id
            WHERE a.resulting_entity_id = :entity AND a.org_id = :org
            """
        ),
        {"entity": uuid.UUID(executed.entity_id or ""), "org": work_org.org_id},
    ).mappings().one()
    assert row["excerpt"] in row["body_text"]
    assert row["agent_identity"] == "extractor"
    assert row["prompt_version"] != "latest"


# --------------------------------------------------------------------------- refusals


def test_an_internal_event_is_never_extracted(
    api: TestClient, as_admin: dict[str, str], roles: None, agent_enabled: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-E-11 loop prevention, at the point it actually bites.

    Without it an AI-proposed Work item becomes a DomainEvent, becomes an Event, and is
    re-extracted into another proposal, forever.
    """
    from app.platform.ids import uuid7

    event_id = uuid7()
    scoped_session.rollback()
    scoped_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(work_org.org_id)},
    )
    scoped_session.execute(
        text(
            "INSERT INTO event (id, org_id, source_system, content_hash, origin, "
            "origin_domain_event_id, type, occurred_at, body_text) VALUES "
            "(:id, :org, 'workos.internal', 'h', 'internal', :dev, 'SYSTEM_ACTIVITY', now(), "
            "'I will send the revised quote')"
        ),
        {"id": event_id, "org": work_org.org_id, "dev": uuid7()},
    )
    scoped_session.commit()

    response = api.post(f"/api/v1/events/{event_id}/analyze", headers=as_admin)
    assert response.status_code == 403
    assert "BR-E-11" in response.text


def test_a_restricted_event_is_not_analysable_by_a_non_participant(
    api: TestClient, realm: Realm, as_admin: dict[str, str], roles: None,
    agent_enabled: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """The agent sees exactly what its delegate sees — which here is nothing (BR-E-08)."""
    event = an_event(
        api,
        as_admin,
        sensitivity="restricted",
        participants=[{"role": "speaker", "person_id": str(work_org.admin)}],
    )
    outsider = auth(realm, subject_of(scoped_session, work_org.outsider), work_org.org_id)
    response = api.post(f"/api/v1/events/{event['id']}/analyze", headers=outsider)
    assert response.status_code == 404, "a refusal would confirm the event exists"


def test_a_viewer_cannot_run_an_analysis(
    api: TestClient, realm: Realm, as_admin: dict[str, str], roles: None,
    agent_enabled: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-AI-03: the agent's reach is its delegate's reach. A viewer proposes nothing."""
    event = an_event(api, as_admin)
    grant(scoped_session, work_org.org_id, work_org.dept_lead, "viewer")
    viewer = auth(realm, subject_of(scoped_session, work_org.dept_lead), work_org.org_id)
    response = api.post(f"/api/v1/events/{event['id']}/analyze", headers=viewer)
    assert response.status_code == 403


def test_an_unknown_agent_is_refused(
    api: TestClient, as_admin: dict[str, str], roles: None,
    worker_session_factory: sessionmaker[Session],
) -> None:
    event = an_event(api, as_admin)
    response = api.post(
        f"/api/v1/events/{event['id']}/analyze?agent=superuser", headers=as_admin
    )
    assert response.status_code == 404


def test_low_confidence_produces_no_proposal(
    api: TestClient, as_admin: dict[str, str], roles: None, agent_enabled: None, work_org: WorkOrg,
    scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """BR-AI-09. Guessing quietly is worse than silence.

    The text has no commitment language, so the provider finds nothing above threshold and the run
    succeeds having proposed nothing — which is a normal outcome, not a failure.
    """
    before = counts(scoped_session, work_org.org_id)
    result = analyze(api, as_admin, an_event(api, as_admin, "The weather was pleasant."))
    after = counts(scoped_session, work_org.org_id)

    assert result["proposal_ids"] == []
    assert after["proposal"] == before["proposal"]
    assert after["ai_interaction"] == before["ai_interaction"] + 1

    interaction = api.get(
        f"/api/v1/ai-interactions/{result['ai_interaction_id']}", headers=as_admin
    ).json()
    assert interaction["status"] == "succeeded"


# --------------------------------------------------------------------------- the record


def test_the_interaction_records_what_ran(
    api: TestClient, as_admin: dict[str, str], roles: None, agent_enabled: None, work_org: WorkOrg,
    worker_session_factory: sessionmaker[Session],
) -> None:
    result = analyze(api, as_admin, an_event(api, as_admin))
    interaction = api.get(
        f"/api/v1/ai-interactions/{result['ai_interaction_id']}", headers=as_admin
    ).json()

    assert interaction["kind"] == "extraction"
    assert interaction["trigger_type"] == "event"
    assert interaction["agent_identity"] == "extractor"
    assert interaction["provider"] == "fake"
    assert interaction["principal_person_id"] == str(work_org.admin)
    assert interaction["prompt_version"] not in ("", "latest")
    assert interaction["status"] == "succeeded"
    assert interaction["output_summary"]["proposals"] >= 1


def test_the_interaction_stores_no_secret_and_no_message_body(
    api: TestClient, as_admin: dict[str, str], roles: None,
    agent_enabled: None, scoped_session: Session,
    worker_session_factory: sessionmaker[Session],
) -> None:
    """An audit row that accumulated a copy of every message would have its own retention story,
    and BR-E-07's purge would leave it behind."""
    secret = "the passphrase is hunter2"
    event = an_event(api, as_admin, f"{PROMISE} {secret}")
    result = analyze(api, as_admin, event)

    scoped_session.rollback()
    row = scoped_session.execute(
        text("SELECT * FROM ai_interaction WHERE id = :id"),
        {"id": uuid.UUID(result["ai_interaction_id"])},
    ).mappings().one()
    rendered = str(dict(row))
    assert secret not in rendered
    assert PROMISE not in rendered


def test_tool_calls_are_recorded_including_refusals(
    api: TestClient, as_admin: dict[str, str], roles: None,
    worker_session_factory: sessionmaker[Session],
    agent_enabled: None,
) -> None:
    """A denial is the authority model working, and is the row worth reading."""
    result = analyze(api, as_admin, an_event(api, as_admin))
    interaction = api.get(
        f"/api/v1/ai-interactions/{result['ai_interaction_id']}", headers=as_admin
    ).json()

    assert interaction["tool_calls"], "a run that proposed something made tool calls"

    by_tool = {call["tool_name"] for call in interaction["tool_calls"]}
    # BR-AI-05: the run looked before it proposed, and the search is its own recorded call — so
    # "did this interaction search first" is answerable from the audit trail rather than by
    # trusting the code did.
    assert "find_similar_work" in by_tool

    for call in interaction["tool_calls"]:
        assert call["authorization_result"] in ("allowed", "denied")
        if call["tool_name"] == "find_similar_work":
            # A read. It runs immediately and succeeds or does not.
            assert call["outcome"] == "succeeded"
        else:
            # A mutation tool. Nothing executed: the Proposal was raised and the tool has not run,
            # and will not until somebody approves it.
            assert call["outcome"] in ("not_attempted", "refused")


def test_another_organizations_interaction_is_invisible(
    api: TestClient, as_admin: dict[str, str], roles: None,
    worker_session_factory: sessionmaker[Session],
) -> None:
    assert api.get(
        f"/api/v1/ai-interactions/{uuid.uuid4()}", headers=as_admin
    ).status_code == 404


# --------------------------------------------------------------------------- confidence (CP10)


def test_a_high_confidence_span_becomes_a_proposal(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    provider_scenario,
) -> None:
    from app.agent.providers.fake import HIGH_CONFIDENCE

    provider_scenario(HIGH_CONFIDENCE)
    result = analyze(api, as_admin, an_event(api, as_admin))
    assert result["proposal_ids"]


def test_a_low_confidence_span_produces_nothing(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    provider_scenario,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """BR-AI-09. Guessing quietly is worse than silence.

    The run succeeds having proposed nothing, which is a normal outcome rather than a failure —
    and the count is recorded so "the model saw something and we chose not to act" stays visible.
    """
    from app.agent.providers.fake import LOW_CONFIDENCE

    provider_scenario(LOW_CONFIDENCE)
    result = analyze(api, as_admin, an_event(api, as_admin))
    assert result["proposal_ids"] == []
    assert result["low_confidence"] >= 1

    interaction = api.get(
        f"/api/v1/ai-interactions/{result['ai_interaction_id']}", headers=as_admin
    ).json()
    assert interaction["status"] == "succeeded"


def test_an_unknown_confidence_never_becomes_a_proposal(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    provider_scenario,
) -> None:
    """The case where the system knows least about what it is doing (ADR-0050).

    A provider that reports no confidence must not be treated as a confident one. `UNKNOWN` is
    refused by the same policy that refuses `LOW`, arrived at honestly rather than by picking a
    number on the provider's behalf.
    """
    from app.agent.providers.fake import UNKNOWN_CONFIDENCE_SCENARIO

    provider_scenario(UNKNOWN_CONFIDENCE_SCENARIO)
    result = analyze(api, as_admin, an_event(api, as_admin))
    assert result["proposal_ids"] == []


def test_the_interaction_records_the_confidence_source(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    provider_scenario,
) -> None:
    """So an evaluation can separate a model's own claim from an adapter's heuristic."""
    from app.agent.providers.fake import HIGH_CONFIDENCE

    provider_scenario(HIGH_CONFIDENCE)
    result = analyze(api, as_admin, an_event(api, as_admin))
    interaction = api.get(
        f"/api/v1/ai-interactions/{result['ai_interaction_id']}", headers=as_admin
    ).json()
    assert interaction["output_summary"]["confidence_source"] == "heuristic"
    assert interaction["output_summary"]["confidence_band"] == "high"


def test_a_provider_timeout_fails_the_request_honestly(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    provider_scenario,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """504, not 500 (ADR-0049).

    "The model provider is unreachable" and "this service has a bug" are different facts, and a
    client that cannot tell them apart cannot decide whether retrying is sensible.

    Nothing is left behind: the request's transaction rolls back, so there is no half-written
    interaction claiming a run that produced nothing. The provider failure is visible as the status
    code and in logs rather than as a row — a known gap, recorded in progress.md.
    """
    from app.agent.providers.fake import TIMES_OUT

    before = counts(scoped_session, work_org.org_id)
    provider_scenario(TIMES_OUT)
    response = api.post(
        f"/api/v1/events/{an_event(api, as_admin)['id']}/analyze", headers=as_admin
    )
    assert response.status_code == 504

    after = counts(scoped_session, work_org.org_id)
    assert after["proposal"] == before["proposal"]
    assert after["evidence"] == before["evidence"]


def test_a_malformed_provider_answer_is_not_a_low_confidence(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    provider_scenario,
    work_org: WorkOrg,
    scoped_session: Session,
) -> None:
    """The distinction the error hierarchy exists for (ADR-0049).

    A broken integration returns 502. If it instead produced a successful run with no proposals,
    an outage would be indistinguishable from a day on which the model found nothing — and nobody
    investigates a quiet day.
    """
    from app.agent.providers.fake import MALFORMED

    before = counts(scoped_session, work_org.org_id)
    provider_scenario(MALFORMED)
    response = api.post(
        f"/api/v1/events/{an_event(api, as_admin)['id']}/analyze", headers=as_admin
    )
    assert response.status_code == 502
    assert "ProviderInvalidResponse" in response.text

    after = counts(scoped_session, work_org.org_id)
    assert after["ai_interaction"] == before["ai_interaction"], (
        "a rolled-back run must not leave an interaction claiming it happened"
    )


def test_the_interaction_records_which_model_answered(
    api: TestClient,
    as_admin: dict[str, str],
    roles: None,
    agent_enabled: None,
    provider_scenario,
) -> None:
    """ADR-0049. What replied, not what was asked for."""
    from app.agent.providers.fake import FakeProvider, FakeScenario

    api.app.state.llm_provider = FakeProvider(  # type: ignore[attr-defined]
        FakeScenario(confidence_value=90), model="deterministic", version="v7"
    )
    result = analyze(api, as_admin, an_event(api, as_admin))
    interaction = api.get(
        f"/api/v1/ai-interactions/{result['ai_interaction_id']}", headers=as_admin
    ).json()
    assert interaction["model"] == "deterministic"
    assert interaction["model_version"] == "v7"
    assert interaction["output_summary"]["model_version_resolved"] is True
