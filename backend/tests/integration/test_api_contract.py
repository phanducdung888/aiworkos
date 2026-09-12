"""Property-based checks over the whole published surface.

Schemathesis reads the OpenAPI document and generates requests from it. What it is good at is the
class of bug nobody writes a test for: the field that is optional in the schema and required in the
handler, the status code that is documented and never returned, the input shape that produces a
traceback instead of a response.

Three properties, and they are deliberately modest:

* nothing ever returns 500 — an unhandled exception is a schema leak (contract §5) before it is a
  bug;
* every response matches what the document promised;
* every error is `problem+json`, because a client that has to guess the error format has no error
  handling.

This is not a security fuzzer. It sends a valid principal and looks for shape defects; finding
authorization holes is the job of the hand-written tests, which know what the right answer is.
"""

from __future__ import annotations

import uuid

import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, Phase, settings
from schemathesis.specs.openapi.checks import (
    negative_data_rejection,
    response_schema_conformance,
)

from tests.integration.conftest import test_application

pytestmark = [pytest.mark.integration, pytest.mark.schemathesis]

schema = schemathesis.openapi.from_asgi("/api/v1/openapi.json", test_application())


def content_type(response: object) -> str:
    """Schemathesis hands headers back as lists of values; httpx hands back strings."""
    raw = getattr(response, "headers", {}).get("content-type", "")
    return raw[0] if isinstance(raw, list) else str(raw)


@schema.parametrize()
@settings(
    max_examples=8,
    deadline=None,
    # The database fixture is shared and the generated cases mutate it, which Hypothesis would
    # otherwise flag. Explicit rather than suppressed globally.
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    phases=[Phase.explicit, Phase.reuse, Phase.generate],
)
def test_no_generated_request_produces_an_unhandled_error(
    case: schemathesis.Case,
    as_admin: dict[str, str],
    roles: None,
) -> None:
    response = case.call(headers=as_admin)

    assert response.status_code < 500, (
        f"{case.method} {case.path} returned {response.status_code}; an unhandled error is a "
        f"description of the schema before it is a bug\n{response.text[:400]}"
    )

    # Only the two checks this test is for. The default suite also asserts things about `Allow`
    # headers and negative-data handling, which are real opinions about HTTP but not the shape
    # defects this is here to catch — and running checks nobody chose produces failures nobody
    # asked a question about.
    case.validate_response(
        response,
        checks=(negative_data_rejection, response_schema_conformance),
    )

    if response.status_code >= 400:
        assert content_type(response).startswith("application/problem+json"), (
            f"{case.method} {case.path} returned {response.status_code} as "
            f"{content_type(response)}; every error is problem+json (D5)"
        )
        body = response.json()
        assert isinstance(body, dict), body
        assert str(body["type"]).startswith("urn:workos:error:"), body
        assert body["status"] == response.status_code


def test_a_garbage_path_parameter_is_a_client_error_not_a_crash(
    api: TestClient, as_admin: dict[str, str], roles: None
) -> None:
    """The commonest shape of hostile input, asserted directly rather than left to generation."""
    for path in (
        "/api/v1/work/not-a-uuid",
        f"/api/v1/projects/{uuid.uuid4()}/milestones",
        "/api/v1/milestones/00000000-0000-0000-0000-000000000000",
    ):
        response = api.get(path, headers=as_admin)
        assert 400 <= response.status_code < 500, f"{path} -> {response.status_code}"
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["type"].startswith("urn:workos:error:")
