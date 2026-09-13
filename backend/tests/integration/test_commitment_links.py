"""Where a promise belongs: the Work it fulfils and the Project it serves (CP27).

The database, the services and the HTTP API have carried `fulfilling_work_id` and `project_id`
since Phase 1. Nothing could set them: the browser offered no control, and the AI's tool contract
did not list them — so in a live organization eight commitments pointed at nothing at all.

These tests are about the two ends that were missing, and about the refusals that have to come with
them. A link that names something that does not exist is worse than no link: it reads as structure
and is noise.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration


def a_commitment(work_org: WorkOrg, **over: object) -> dict[str, object]:
    return {
        "statement": "I will finish the migration runbook",
        "committed_by_person_id": str(work_org.member),
        "due_precision": "vague",
        **over,
    }


def a_work_item(api: TestClient, as_admin: dict[str, str], title: str) -> str:
    created = api.post(
        "/api/v1/work",
        json={"title": title},
        headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


class TestLinkingAPromiseToWork:
    def test_a_commitment_can_be_created_already_linked(
        self, api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
    ) -> None:
        work_id = a_work_item(api, as_admin, f"Runbook {uuid.uuid4().hex[:6]}")
        created = api.post(
            "/api/v1/commitments",
            json=a_commitment(work_org, fulfilling_work_id=work_id),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert created.status_code == 201, created.text
        assert created.json()["fulfilling_work_id"] == work_id

    def test_a_commitment_can_be_linked_afterwards(
        self, api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
    ) -> None:
        """The ordinary case. A promise is recorded when somebody makes it; which work it serves
        is usually known later, and by a person rather than by the AI."""
        work_id = a_work_item(api, as_admin, f"Runbook {uuid.uuid4().hex[:6]}")
        created = api.post(
            "/api/v1/commitments",
            json=a_commitment(work_org),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        ).json()
        assert created["fulfilling_work_id"] is None

        linked = api.patch(
            f"/api/v1/commitments/{created['id']}",
            json={"fulfilling_work_id": work_id},
            headers={**as_admin, "If-Match": f'W/"{created["version"]}"'},
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["fulfilling_work_id"] == work_id

    def test_the_link_is_findable_from_the_work_side(
        self, api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
    ) -> None:
        """The read that makes a Work screen able to show its promises."""
        work_id = a_work_item(api, as_admin, f"Runbook {uuid.uuid4().hex[:6]}")
        api.post(
            "/api/v1/commitments",
            json=a_commitment(work_org, fulfilling_work_id=work_id),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        listed = api.get(
            f"/api/v1/commitments?fulfilling_work_id={work_id}", headers=as_admin
        )
        assert listed.status_code == 200, listed.text
        assert [row["fulfilling_work_id"] for row in listed.json()["items"]] == [work_id]


class TestARefusalIsBetterThanANoisyLink:
    def test_work_that_does_not_exist_is_refused_on_create(
        self, api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
    ) -> None:
        refused = api.post(
            "/api/v1/commitments",
            json=a_commitment(work_org, fulfilling_work_id=str(uuid.uuid4())),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert refused.status_code == 422, refused.text
        assert "fulfilling_work_id" in refused.text

    def test_a_project_that_does_not_exist_is_refused_on_create(
        self, api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
    ) -> None:
        """CP27. A composite foreign key already refused this, as a 500 and a stack trace. Now
        that an approved AI action may carry `project_id`, the caller is told which field is wrong.
        """
        refused = api.post(
            "/api/v1/commitments",
            json=a_commitment(work_org, project_id=str(uuid.uuid4())),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert refused.status_code == 422, refused.text
        assert "project_id" in refused.text

    def test_a_project_that_does_not_exist_is_refused_on_update_too(
        self, api: TestClient, as_admin: dict[str, str], roles: None, work_org: WorkOrg
    ) -> None:
        """`update` validated Work and not Project — the asymmetry CP27 found while opening the
        tool contract."""
        created = api.post(
            "/api/v1/commitments",
            json=a_commitment(work_org),
            headers={**as_admin, "Idempotency-Key": str(uuid.uuid4())},
        ).json()
        refused = api.patch(
            f"/api/v1/commitments/{created['id']}",
            json={"project_id": str(uuid.uuid4())},
            headers={**as_admin, "If-Match": f'W/"{created["version"]}"'},
        )
        assert refused.status_code == 422, refused.text
        assert "project_id" in refused.text


class TestWhatTheApprovedActionMayCarry:
    def test_the_tool_contract_lists_both_links(self) -> None:
        """ADR-0042's registry is a contract about what an approved action may carry.

        Listing them is not a claim that the AI fills them in — BR-AI-17 forbids inferring a
        project the text does not state. It opens the path that was closed: a reviewer adding the
        link with `edited_arguments` before approving, recorded in the ApprovalRecord like any
        other edit (ADR-0041).
        """
        from app.contexts.intelligence.public import REGISTRY

        tool = REGISTRY[("create_commitment", "v1")]
        assert {"fulfilling_work_id", "project_id"} <= tool.optional_arguments
        # Still not required: a promise whose home is unknown is a complete record (BR-C-08).
        assert tool.required_arguments == {"statement", "committed_by_person_id"}
