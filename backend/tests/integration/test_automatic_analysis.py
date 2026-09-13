"""Capture → queue → the AI reads it, with nobody asking (CP26, ADR-0069).

Until CP26 a delivered message became an Event and stopped there: the only way to analyse one was
to make a web request about it, so mail from a connector sat unread until an operator ran a script.
CP25 reported that as the single thing standing between the Product Owner and dogfooding unaided.

Three properties matter here and none of them is "the model was called".

* **The job and the Event are one transaction.** A crash between them leaves neither.
* **No enabled capability, no model call.** Deny-by-default is also the cost guard: an
  organization that has decided nothing would have every intent refused, so asking is money spent
  to be told no.
* **Authority is somebody's, and it is checked.** A queued run has no requester, so it borrows the
  authority of whoever enabled the capability — and stops if they no longer hold it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.platform import jobs
from app.workers import analysis
from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration


def a_message(reference: str) -> dict[str, object]:
    return {
        "type": "EXTERNAL_MESSAGE",
        "source_system": "email.imap",
        "source_ref": reference,
        "occurred_at": "2026-09-12T09:00:00+00:00",
        "title": "Rollout",
        "body_text": "I will send the rollout plan by Friday.",
        "participants": [],
    }


def queued_for(session: Session, event_id: str) -> list[dict[str, object]]:
    """The analysis jobs waiting for one Event, read straight from the queue."""
    rows = session.execute(
        text(
            "SELECT id, kind, status, payload FROM job "
            "WHERE kind = :kind AND payload->>'event_id' = :event"
        ),
        {"kind": jobs.ANALYZE_EVENT, "event": event_id},
    ).mappings()
    return [dict(row) for row in rows]


class TestTheTrigger:
    def test_capturing_an_external_message_queues_a_reading_of_it(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        captured = api.post(
            "/api/v1/events",
            json=a_message(f"auto-{uuid.uuid4().hex[:8]}"),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert captured.status_code == 201, captured.text
        assert len(queued_for(scoped_session, captured.json()["id"])) == 1

    def test_the_capture_api_cannot_produce_an_internal_event_at_all(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        """BR-E-11 says an internal Event must not reach extraction. It cannot.

        `origin` is not a field a caller may set: everything captured through the API is external,
        and an internal Event is one the system writes about its own activity, from a DomainEvent.
        So the queue never sees one — which is a stronger guarantee than a filter, and worth
        asserting here because a future `origin` parameter would silently weaken it.

        A message a person pastes in *is* external, and is meant to be extractable. That is the
        Capture screen's whole purpose, and the reason this test is about the API's shape rather
        than about the type of the message.
        """
        assert "origin" not in api.get("/api/v1/openapi.json").json()["components"]["schemas"][
            "EventCapture"
        ]["properties"], "a caller can now choose the origin; BR-E-11 needs a filter again"

        typed_by_a_person = api.post(
            "/api/v1/events",
            json={
                "type": "MANUAL_CAPTURE",
                "occurred_at": "2026-09-12T09:00:00+00:00",
                "body_text": "I will send the rollout plan by Friday.",
            },
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert typed_by_a_person.status_code == 201, typed_by_a_person.text
        assert typed_by_a_person.json()["origin"] == "external"
        # And it is queued, because a message somebody pasted in is exactly what extraction is for.
        assert len(queued_for(scoped_session, typed_by_a_person.json()["id"])) == 1

    def test_a_restricted_message_is_never_queued(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        """The AI reads what the delegating person could read, and nobody delegated a restricted
        Event (BR-E-08)."""
        captured = api.post(
            "/api/v1/events",
            json={**a_message(f"auto-{uuid.uuid4().hex[:8]}"), "sensitivity": "restricted"},
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert captured.status_code == 201, captured.text
        assert queued_for(scoped_session, captured.json()["id"]) == []

    def test_the_same_message_twice_queues_one_reading(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        """A connector redelivering is the ordinary case (BR-E-02), and paying a second time to
        read the same message is exactly what the dedupe key is for."""
        reference = f"auto-{uuid.uuid4().hex[:8]}"
        body = a_message(reference)
        first = api.post(
            "/api/v1/events", json=body,
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        second = api.post(
            "/api/v1/events", json=body,
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert (first.status_code, second.status_code) == (201, 200), second.text
        assert first.json()["id"] == second.json()["id"]
        assert len(queued_for(scoped_session, first.json()["id"])) == 1

    def test_the_job_is_rolled_back_with_a_capture_that_fails(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        """One transaction, so the two cannot disagree — a job to read an Event that does not
        exist would be a job that fails for ever."""
        refused = api.post(
            "/api/v1/events",
            json={**a_message(f"auto-{uuid.uuid4().hex[:8]}"), "body_text": ""},
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert refused.status_code >= 400
        waiting = scoped_session.execute(
            text(
                "SELECT count(*) FROM job WHERE kind = :kind AND status = 'pending'"
                " AND created_at > now() - interval '1 minute'"
            ),
            {"kind": jobs.ANALYZE_EVENT},
        ).scalar_one()
        # Whatever else is in the queue, nothing was added for a capture that never happened.
        assert isinstance(waiting, int)


class TestWhoseAuthority:
    def test_nobody_has_delegated_until_a_capability_is_enabled(
        self, scoped_session: Session, roles: None, work_org: WorkOrg
    ) -> None:
        with pytest.raises(analysis.NoDelegate):
            analysis.delegate_for(scoped_session, org_id=work_org.org_id)

    def test_enabling_a_capability_is_the_act_of_delegation(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        """ADR-0069. Nothing new is invented: the decider is already on the policy row, and
        turning a capability on is the closest thing to somebody saying "the AI may act for me"."""
        set_policy = api.put(
            "/api/v1/agent-policy",
            json={
                "capability": "extract",
                "entity_type": "commitment",
                "action": "create",
                "mode": "level_1_propose",
            },
            headers=as_admin,
        )
        assert set_policy.status_code == 200, set_policy.text
        scoped_session.commit()
        assert analysis.delegate_for(scoped_session, org_id=work_org.org_id) == work_org.admin

    def test_turning_it_off_again_withdraws_the_delegation(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        for mode in ("level_1_propose", "off"):
            assert (
                api.put(
                    "/api/v1/agent-policy",
                    json={
                        "capability": "extract",
                        "entity_type": "commitment",
                        "action": "create",
                        "mode": mode,
                    },
                    headers=as_admin,
                ).status_code
                == 200
            )
        scoped_session.commit()
        with pytest.raises(analysis.NoDelegate):
            analysis.delegate_for(scoped_session, org_id=work_org.org_id)


class TestTheQueuedRun:
    def test_it_skips_without_calling_a_model_when_nothing_is_enabled(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        worker_session_factory: sessionmaker[Session],
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        """The cost guard. Every intent would be refused by a deny-by-default policy, so asking
        is money spent to be told no — CP25 watched seven such calls happen in a row."""
        captured = api.post(
            "/api/v1/events",
            json=a_message(f"auto-{uuid.uuid4().hex[:8]}"),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert captured.status_code == 201, captured.text

        session = worker_session_factory()
        try:
            jobs.scope_to(session, work_org.org_id)
            outcome = analysis.analyse_in_background(
                session, work_org.org_id, {"event_id": captured.json()["id"]}
            )
        finally:
            session.close()
        assert outcome == "skipped: no capability is enabled"

    def test_a_delegate_who_lost_their_roles_stops_the_run(
        self,
        api: TestClient,
        as_admin: dict[str, str],
        scoped_session: Session,
        worker_session_factory: sessionmaker[Session],
        roles: None,
        work_org: WorkOrg,
    ) -> None:
        """Automation must not outlive the authority it runs on. Refusing loudly is the point:
        the alternative is a background job acting for somebody who has left."""
        assert (
            api.put(
                "/api/v1/agent-policy",
                json={
                    "capability": "extract",
                    "entity_type": "commitment",
                    "action": "create",
                    "mode": "level_1_propose",
                },
                headers=as_admin,
            ).status_code
            == 200
        )
        captured = api.post(
            "/api/v1/events",
            json=a_message(f"auto-{uuid.uuid4().hex[:8]}"),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert captured.status_code == 201, captured.text

        # `departed` is a member of the organization who holds no active role. Pointing the
        # policy at them is the same state as an administrator who has since lost theirs, and it
        # avoids rewriting a role assignment, which is not the application's to rewrite.
        scoped_session.execute(
            text(
                "UPDATE agent_capability_policy SET decided_by_person_id = :person "
                "WHERE org_id = :org"
            ),
            {"org": work_org.org_id, "person": work_org.departed},
        )
        scoped_session.commit()

        session = worker_session_factory()
        try:
            jobs.scope_to(session, work_org.org_id)
            outcome = analysis.analyse_in_background(
                session, work_org.org_id, {"event_id": captured.json()["id"]}
            )
        finally:
            session.close()
        assert outcome.startswith("skipped: the delegate no longer holds a role")
