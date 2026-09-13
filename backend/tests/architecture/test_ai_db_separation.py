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

    `${VAR:?...}` fails the deploy with an explanation instead, which is the behaviour worth having
    when the alternative is a connector silently starting with somebody's example password.
    """
    import re

    raw = COMPOSE.read_text()
    services = _compose().get("services", {})
    for name in _connectors(services):
        block = raw[raw.index(f"  {name}:") :]
        block = block[: block.find("\n  api:")] if "\n  api:" in block else block
        for secret in ("PASSWORD", "TOKEN", "SECRET"):
            for match in re.finditer(rf"\$\{{\w*{secret}\w*(:?[-?])", block):
                assert match.group(1) == ":?", (
                    f"{name} defaults a {secret.lower()}; it must be required, not defaulted"
                )
