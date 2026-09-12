"""The published API surface, checked as a whole.

A committed OpenAPI document turns "the API changed" into a line in a diff. Without one, an endpoint
can appear, a field can vanish and a status code can move without anybody reviewing it — the code
review would show a decorator, not the contract it publishes.

The snapshot is regenerated with `make openapi`. Regenerating it is the point: the burden is one
command, and the value is that nobody does it by accident.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

SNAPSHOT = Path(__file__).resolve().parents[2] / "openapi.json"


def test_the_openapi_snapshot_matches_the_running_application(api: TestClient) -> None:
    """Contract §19: OpenAPI is part of done, not a by-product of it."""
    generated = api.get("/api/v1/openapi.json").json()
    assert SNAPSHOT.exists(), "openapi.json is missing; run `make openapi`"
    committed = json.loads(SNAPSHOT.read_text())

    assert set(generated["paths"]) == set(committed["paths"]), (
        "the set of endpoints changed; run `make openapi` and review the diff"
    )
    for path, operations in generated["paths"].items():
        assert set(operations) == set(committed["paths"][path]), (
            f"the methods on {path} changed; run `make openapi`"
        )
    assert generated == committed, (
        "the OpenAPI document has drifted from openapi.json; run `make openapi` and review"
    )


def test_every_published_path_is_one_of_the_five_work_core_aggregates(
    api: TestClient,
) -> None:
    """Phase 1 publishes the Work Core and nothing else.

    An endpoint for Events, Evidence, Proposals or anything AI-shaped is Phase 2 or later (contract
    §18). This fails the moment one appears, which is earlier than a reviewer would notice it.
    """
    paths = set(api.get("/api/v1/openapi.json").json()["paths"])
    allowed_prefixes = (
        # Work Core
        "/api/v1/work",
        "/api/v1/projects",
        "/api/v1/milestones",
        "/api/v1/dependencies",
        # Identity & Organization, read-only: every Work Core write names a Person, a Team or a
        # Department, so a client cannot complete a journey without looking them up.
        "/api/v1/me",
        "/api/v1/people",
        "/api/v1/teams",
        "/api/v1/departments",
        "/health",
    )
    unexpected = [p for p in paths if not p.startswith(allowed_prefixes)]
    assert not unexpected, f"unexpected endpoints published: {sorted(unexpected)}"
