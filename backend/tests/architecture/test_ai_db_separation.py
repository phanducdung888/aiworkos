"""The invariant that has to hold before there is anything to violate it.

ADR-0002: the AI never reaches the database. Three independent mechanisms are meant to enforce that
— network topology, absent credentials, and code boundaries. None of them exists usefully if they
arrive at the same time as the agent runtime, because by then the convenient shortcut has already
been taken and is already load-bearing.

So these tests are written in Phase 1 and pass vacuously today. They stop passing vacuously the
moment somebody adds `agent/` or `app/tools/`, which is exactly when the argument would otherwise
start.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
COMPOSE = REPO_ROOT / "docker-compose.yml"
OBJECT_PROXY = REPO_ROOT / "ops" / "proxy" / "objects.conf"

DATA_NETWORK = "data"
AGENT_SERVICE_NAMES = {"agent-runtime", "agent", "openclaw"}
#: Anything that delivers messages in from outside (ADR-0058). Named the same way the agent
#: services are, and for the same reason: the rule has to hold for the service that does not
#: exist yet.
CONNECTOR_SERVICE_SUFFIX = "-connector"
DB_ENV_MARKERS = ("DATABASE_URL", "POSTGRES_", "PGHOST", "PGPASSWORD", "REDIS_URL", "MINIO_")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_the_data_network_exists_and_is_declared() -> None:
    compose = _compose()
    assert DATA_NETWORK in compose.get("networks", {}), (
        "the data network is a security boundary; it must be declared explicitly"
    )


def test_no_agent_service_is_attached_to_the_data_network() -> None:
    compose = _compose()
    services = compose.get("services", {})
    present = [name for name in services if name in AGENT_SERVICE_NAMES]
    if not present:
        # Vacuous today. The assertion below is what matters in Phase 3.
        assert True
        return
    for name in present:
        networks = set(services[name].get("networks") or [])
        assert DATA_NETWORK not in networks, (
            f"{name} is attached to the {DATA_NETWORK} network; ADR-0002 forbids it"
        )


def test_no_agent_service_receives_data_tier_credentials() -> None:
    compose = _compose()
    services = compose.get("services", {})
    for name in (n for n in services if n in AGENT_SERVICE_NAMES):
        environment = services[name].get("environment") or {}
        keys = environment.keys() if isinstance(environment, dict) else [
            entry.split("=", 1)[0] for entry in environment
        ]
        for key in keys:
            assert not any(marker in key for marker in DB_ENV_MARKERS), (
                f"{name} is given {key}; the agent runtime holds no data-tier credentials"
            )


def test_agent_code_never_imports_the_data_layer() -> None:
    """Covers `agent/` and `backend/app/tools/` before either exists."""
    roots = [REPO_ROOT / "agent", BACKEND_ROOT / "app" / "tools"]
    forbidden = ("sqlalchemy", "psycopg", "app.platform.db", "app.contexts", "redis", "minio",
        "boto3")
    checked = 0
    for root in roots:
        if not root.exists():
            continue
        for module in root.rglob("*.py"):
            if "__pycache__" in module.parts:
                continue
            checked += 1
            tree = ast.parse(module.read_text(), filename=str(module))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            offending = {i for i in imported if i.startswith(forbidden)}
            if root.name == "tools":
                # The Tool Gateway may call application services. It may not reach further down.
                offending = {i for i in offending if not i.startswith("app.services")}
            assert not offending, f"{module.relative_to(REPO_ROOT)} imports {sorted(offending)}"
    assert checked >= 0


def test_no_autonomy_level_above_two_is_representable() -> None:
    """BR-AI-31. Checked across the whole backend so it cannot be introduced in a config module."""
    banned = ("level_3", "fully_autonomous", "ai_autonomous", "auto_apply")
    for module in BACKEND_ROOT.rglob("*.py"):
        if "__pycache__" in module.parts or "tests" in module.parts:
            continue
        lowered = module.read_text().lower()
        for token in banned:
            assert token not in lowered, (
                f"{module.relative_to(REPO_ROOT)} contains '{token}'; the MVP ceiling is "
                "level_2_approved_execution (BR-AI-30, BR-AI-31)"
            )


def _defaults(value: str) -> str:
    """`${VAR:-9002}` as `9002`.

    Compose interpolates before anything reads this file; a test that reads the YAML directly sees
    the placeholder, and comparing placeholders would pass whatever they expand to.
    """
    import re as _re

    return _re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}", r"\1", value)


def _connectors(services: dict) -> list[str]:
    return [name for name in services if name.endswith(CONNECTOR_SERVICE_SUFFIX)]


def test_no_connector_is_attached_to_the_data_network() -> None:
    """ADR-0058, as topology rather than as discipline.

    A connector speaks a vendor's protocol on one side, which is the least trustworthy input this
    system has. It reaches WorkOS over HTTP like any other client, so it has no business on the
    network where PostgreSQL, Redis and MinIO live — the same argument ADR-0002 makes about the
    agent runtime, applied to the other untrusted edge.
    """
    services = _compose().get("services", {})
    for name in _connectors(services):
        networks = set(services[name].get("networks") or [])
        assert DATA_NETWORK not in networks, (
            f"{name} is attached to the {DATA_NETWORK} network; a connector posts to the API"
        )


def test_a_connector_can_actually_reach_the_api() -> None:
    """The other half of the confinement, and the half CP21 forgot.

    `test_no_connector_is_attached_to_the_data_network` was satisfied by a connector that shared no
    network with the API either — so it was compliant and could not have delivered a single message.
    A rule that only says where something may *not* go is satisfied by putting it nowhere.
    """
    services = _compose().get("services", {})
    api_networks = set(services["api"].get("networks") or [])
    for name in _connectors(services):
        networks = set(services[name].get("networks") or [])
        assert networks & api_networks, (
            f"{name} shares no network with the API; it could not post an Event"
        )


def test_no_connector_receives_data_tier_credentials() -> None:
    """It holds a bearer token for one organization and nothing else (ADR-0060)."""
    services = _compose().get("services", {})
    for name in _connectors(services):
        environment = services[name].get("environment") or {}
        keys = (
            environment.keys()
            if isinstance(environment, dict)
            else [entry.split("=", 1)[0] for entry in environment]
        )
        for key in keys:
            assert not any(marker in key for marker in DB_ENV_MARKERS), (
                f"{name} is given {key}; a connector holds no data-tier credential"
            )


def test_no_connector_service_carries_a_default_credential() -> None:
    """A default for a password or a token in this file would be a secret in the repository.

    `${VAR}` with nothing after it is what that looks like: the variable is passed through, and the
    connector refuses to start with a message naming what is missing.

    Not `${VAR:?...}`, which CP21 used and CP22 removed. Compose interpolates the whole file before
    it applies profiles, so a required-variable marker on a `pilot`-profile service failed every
    `docker compose` command — including `up postgres` — for anyone who had not set it.
    """
    import re

    raw = COMPOSE.read_text()
    services = _compose().get("services", {})
    for name in _connectors(services):
        block = raw[raw.index(f"  {name}:") :]
        end = block.find("\n  api:")
        block = block[:end] if end != -1 else block
        for secret in ("PASSWORD", "TOKEN", "SECRET"):
            for match in re.finditer(rf"\$\{{(\w*{secret}\w*)([^}}]*)\}}", block):
                # A name, not a value. `WORKOS_OIDC_TOKEN_URL` is where to ask for a token, which
                # is deployment topology and belongs in this file with a working default; the
                # client secret next to it is the credential and still may not have one.
                if match.group(1).endswith("_URL"):
                    continue
                assert match.group(2) == "", (
                    f"{name} gives {match.group(1)} a default or a marker: "
                    f"a credential is passed through or not at all"
                )


# --------------------------------------------------------------------------- object storage


def test_the_object_store_stays_on_the_data_network() -> None:
    """ADR-0063. Attachments are reached through a proxy, never by joining the store to the world.

    The alternative that keeps being tempting — put MinIO on `app` so clients can reach it — makes
    the data network's boundary meaningless the moment somebody wants a file.
    """
    services = _compose().get("services", {})
    assert set(services["minio"].get("networks") or []) == {DATA_NETWORK}


def test_only_the_object_proxy_spans_the_data_boundary() -> None:
    """One crossing, and it is a forwarder that holds no credential and decides nothing.

    The API spans it too, of course — it is the application. What must not accumulate is a second,
    third and fourth service with a reason to be on both sides.
    """
    services = _compose().get("services", {})
    spanning = {
        name
        for name, service in services.items()
        if DATA_NETWORK in set(service.get("networks") or [])
        and set(service.get("networks") or []) - {DATA_NETWORK}
    }
    assert spanning == {"api", "objects"}, (
        f"{sorted(spanning)} straddle the data network; only the API and the object proxy may"
    )


def test_a_connector_reaches_object_storage_only_through_the_proxy() -> None:
    """The whole point of the topology, stated where it can fail.

    A connector must be able to complete a presigned upload — and must not be able to address the
    store to do it.
    """
    services = _compose().get("services", {})
    proxy_networks = set(services["objects"].get("networks") or [])
    store_networks = set(services["minio"].get("networks") or [])
    for name in _connectors(services):
        networks = set(services[name].get("networks") or [])
        assert networks & proxy_networks, f"{name} cannot reach the object proxy"
        assert not networks & store_networks, f"{name} can address the object store directly"


def test_the_api_signs_urls_for_hosts_its_clients_can_actually_reach() -> None:
    """A presigned URL signed for a name the holder cannot resolve is unusable, and unfixable.

    The signature covers the host, so this is not something a deployment can paper over later by
    rewriting the URL — every address has to be configured at signing time.

    There are two kinds of client and they are on different networks. A connector uploads from the
    application network and addresses the proxy by its service name. A browser downloads from a
    person's machine and cannot resolve a Docker service name at all — CP24 signed both for
    `objects:9000` and CP25 found the consequence the first time anybody opened an attachment
    (ADR-0067).
    """
    services = _compose()["services"]
    environment = services["api"].get("environment") or {}
    assert "minio" in environment["WORKOS_S3_ENDPOINT_URL"], (
        "this process talks to the store directly"
    )
    assert "objects" in environment["WORKOS_S3_UPLOAD_ENDPOINT_URL"], (
        "a connector reaches the proxy by its service name"
    )
    public = environment["WORKOS_S3_PUBLIC_ENDPOINT_URL"]
    assert "objects" not in public and "minio" not in public, (
        f"{public} is a Docker service name; no browser will ever resolve it"
    )

    # And the address it is signed for has to be one the proxy is actually published on.
    published = [_defaults(str(entry)) for entry in services["objects"].get("ports") or []]
    port = _defaults(public).rsplit(":", 1)[-1].rstrip("/")
    assert any(entry.split(":")[-2] == port for entry in published), (
        f"URLs are signed for port {port} but the object proxy publishes {published}"
    )


def test_the_object_proxy_does_not_log_presigned_urls() -> None:
    """A presigned URL is a credential, and an access log is a file anyone with the host can read.

    nginx's default `combined` format logs `$request` — the whole request line, query string
    included — so every upload and download left a working capability for that object in
    `docker compose logs objects`. CP24 found twenty of them. `$uri` is the same line with the
    query removed.
    """
    config = OBJECT_PROXY.read_text()
    assert "$uri" in config, "the proxy must log the path without its query string"
    assert "$request " not in config and "$request'" not in config, (
        "`$request` is the whole request line; a presigned URL's signature is in the query string"
    )
    assert "access_log" in config, (
        "without an explicit access_log directive nginx uses `combined`, which logs `$request`"
    )


# --------------------------------------------------------------------------- the web surface


def test_the_web_surface_holds_no_data_tier_credential() -> None:
    """It serves static files and forwards HTTP. Nothing it does needs a database.

    Stated here rather than assumed because the temptation is specific and recurring: the surface a
    person looks at is the one somebody eventually wants to "just read one more field" from.
    """
    web = _compose()["services"]["web"]
    assert DATA_NETWORK not in set(web.get("networks") or []), (
        "the web surface is on the data network; it proxies to the API like any other client"
    )
    environment = web.get("environment") or {}
    keys = (
        environment.keys()
        if isinstance(environment, dict)
        else [entry.split("=", 1)[0] for entry in environment]
    )
    for key in keys:
        assert not any(marker in key for marker in DB_ENV_MARKERS), f"web is given {key}"


def test_the_sign_in_redirect_matches_the_port_the_web_surface_is_published_on() -> None:
    """Three files have to agree, and two of them fail silently when they do not.

    The redirect URI is baked into the bundle at build time, Keycloak checks it against the realm's
    list, and the browser has to be able to reach it. A mismatch is an identity provider refusing a
    sign-in that looks correct, with the wrong port visible only in a query string — so it is
    cheaper to fail here.
    """
    import json
    import re

    services = _compose()["services"]
    published = [_defaults(str(entry)) for entry in services["web"].get("ports") or []]
    host_ports = {entry.split(":")[-2] for entry in published}

    redirect = _defaults(services["web"]["build"]["args"]["VITE_OIDC_REDIRECT_URI"])
    port = re.search(r"://[^/:]+:(\d+)", redirect)
    assert port and port.group(1) in host_ports, (
        f"sign-in returns to {redirect} but the web surface publishes {sorted(host_ports)}"
    )

    realm = json.loads((REPO_ROOT / "ops" / "keycloak" / "realm.json").read_text())
    web_client = next(c for c in realm["clients"] if c["clientId"] == "workos-web")
    assert any(
        redirect.startswith(uri.rstrip("*")) for uri in web_client["redirectUris"]
    ), f"{redirect} is not covered by {web_client['redirectUris']}"
