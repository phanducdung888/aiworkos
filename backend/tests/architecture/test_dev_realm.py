"""The development realm has to be a realm Keycloak will actually import.

Both tests below are regressions for defects CP24 found by running the stack rather than by reading
it. Neither was visible to any other test, because the suite never starts Keycloak: it signs its
own tokens with a throwaway key (testing-strategy T-3). That is still the right trade — but it
means the one file that has to be right for a real sign-in had nothing checking it at all.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[3]
REALM = REPO_ROOT / "ops" / "keycloak" / "realm.json"
SEED = REPO_ROOT / "ops" / "dev" / "seed.py"


def _realm() -> dict[str, Any]:
    return json.loads(REALM.read_text())


def _walk(node: Any) -> list[str]:
    """Every key in the document, at any depth."""
    if isinstance(node, dict):
        return [k for k in node] + [k for v in node.values() for k in _walk(v)]
    if isinstance(node, list):
        return [k for v in node for k in _walk(v)]
    return []


def test_the_realm_carries_no_field_keycloak_would_reject() -> None:
    """JSON has no comments, so somebody invents one. Keycloak's import is strict and exits.

    A top-level `_comment` block sat in this file from Checkpoint 4 until CP24 and aborted every
    import with `UnrecognizedPropertyException`. Because `start-dev` then exits, the symptom is a
    Keycloak that will not start — which reads as broken infrastructure, not as a typo in a fixture.
    Commentary belongs in `ops/keycloak/README.md`.
    """
    invented = sorted({key for key in _walk(_realm()) if key.startswith("_")})
    assert not invented, (
        f"{invented} are not Keycloak fields; the import aborts on the first one it does not know"
    )


def _seeded_subjects() -> dict[str, str]:
    """`PEOPLE` from the seed script, read as data rather than imported.

    The seed reaches into `backend/` on import and connects to nothing until `main()`, but reading
    the literal keeps this test independent of that.
    """
    tree = ast.parse(SEED.read_text(), filename=str(SEED))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PEOPLE" for t in node.targets
        ):
            people = ast.literal_eval(node.value)
            return {username: subject for subject, username, _name, _role in people}
    raise AssertionError("ops/dev/seed.py no longer defines PEOPLE")


def test_every_seeded_person_carries_the_subject_keycloak_will_issue() -> None:
    """`resolve_principal` looks a Person up by the token's `sub`, and `sub` is the user's id.

    The seed wrote usernames until CP24, so a real token resolved to no Person and every
    authenticated request 404ed on an organization the person was plainly a member of. Nothing
    caught it: each test seeds a Person and then signs a token with that same subject, so the two
    agree by construction and the question of what Keycloak actually issues never arises.
    """
    realm_ids = {user["username"]: user.get("id") for user in _realm()["users"]}
    for username, subject in _seeded_subjects().items():
        assert username in realm_ids, f"{username} is seeded but is not in the dev realm"
        assert realm_ids[username] == subject, (
            f"{username} is seeded with subject {subject!r} but the realm issues "
            f"{realm_ids[username]!r}"
        )


def test_the_connector_service_account_is_pinned_and_ingestion_only() -> None:
    """ADR-0060. The connector's identity is a Person with one role, and that has to be *this* one.

    A service account whose id Keycloak mints at import cannot be seeded ahead of time, so the
    alternative to pinning it is a manual step between starting the stack and being able to ingest
    anything — which is the kind of step that gets skipped and then blamed on the connector.
    """
    realm = _realm()
    connector = next(c for c in realm["clients"] if c["clientId"] == "workos-connector")
    assert connector["serviceAccountsEnabled"] is True
    account = next(
        u for u in realm["users"] if u["username"] == "service-account-workos-connector"
    )
    assert account.get("serviceAccountClientId") == "workos-connector"
    assert account.get("id"), "the service account needs a pinned id, or its `sub` is unknowable"

    people = ast.literal_eval(
        next(
            node.value
            for node in ast.walk(ast.parse(SEED.read_text()))
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "PEOPLE" for t in node.targets)
        )
    )
    roles = {username: role for _subject, username, _name, role in people}
    assert roles["service-account-workos-connector"] == "ingestion"


def test_the_realm_does_not_displace_keycloaks_built_in_client_scopes() -> None:
    """A `clientScopes` block in an imported realm *replaces* the built-in set; it does not add.

    The realm carried one, holding only `workos-audience`, and both clients then listed `profile`,
    `email` and `roles` as default scopes. Those names resolved to nothing, silently: the realm had
    two scopes in it and no `basic`, so tokens came back with no `sub` and no `email` — the two
    claims this application consumes — and the SPA's `openid profile email` request was for scopes
    the realm did not have. The audience mapper belongs on the clients, where it is not competing
    with Keycloak's defaults.
    """
    realm = _realm()
    defined = {scope["name"] for scope in realm.get("clientScopes", [])}
    if not defined:
        for client in realm["clients"]:
            assert "defaultClientScopes" not in client, (
                f"{client['clientId']} pins default scopes but the realm defines none of its own; "
                "Keycloak's built-ins are what those names have to come from"
            )
        return
    for client in realm["clients"]:
        missing = set(client.get("defaultClientScopes", [])) - defined
        assert not missing, (
            f"{client['clientId']} asks for {sorted(missing)}, which this realm replaced the "
            "built-in scopes without defining"
        )


def test_every_token_issuing_client_names_the_api_as_an_audience() -> None:
    """Audience is one of the four things the API checks, and nothing else puts it in the token."""
    for client in _realm()["clients"]:
        if client["clientId"] == "workos-api":
            continue  # It is the audience; it issues nothing.
        mappers = client.get("protocolMappers") or []
        audiences = [m for m in mappers if m["protocolMapper"] == "oidc-audience-mapper"]
        assert audiences, f"{client['clientId']} mints tokens the API will reject on audience"
        assert any(
            m["config"].get("included.client.audience") == "workos-api" for m in audiences
        ), f"{client['clientId']} names some other audience"
