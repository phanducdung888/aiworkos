"""Boundaries, enforced by tooling rather than by discipline.

A modular monolith is only modular while something fails when a boundary is crossed. Nothing
physical stops an import here, so these tests are the boundary (ADR-0001).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP = BACKEND_ROOT / "app"


def _modules(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_import_linter_contracts_pass() -> None:
    result = subprocess.run(
        ["lint-imports", "--config", ".importlinter"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 127:  # pragma: no cover
        pytest.skip("import-linter is not installed")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_platform_never_imports_a_bounded_context() -> None:
    """Platform is underneath everything. An import upwards is a layering inversion."""
    for module in _modules(APP / "platform"):
        offending = {i for i in _imports(module) if i.startswith("app.contexts")}
        assert not offending, f"{module.relative_to(BACKEND_ROOT)} imports {sorted(offending)}"


def test_contexts_do_not_import_each_other_directly() -> None:
    """Cross-context access goes through the other context's `public` module, or not at all."""
    contexts = [p for p in (APP / "contexts").iterdir() if p.is_dir() and not \
        p.name.startswith("_")]
    for context in contexts:
        own_prefix = f"app.contexts.{context.name}"
        for module in _modules(context):
            for imported in _imports(module):
                if not imported.startswith("app.contexts."):
                    continue
                if imported.startswith(own_prefix):
                    continue
                assert imported.endswith(".public"), (
                    f"{module.relative_to(BACKEND_ROOT)} reaches into {imported}; "
                    "cross-context imports must go through a public interface module"
                )


def test_identity_never_imports_the_work_core() -> None:
    """The cross-context exception runs one way only.

    Work Core is downstream of Identity: it asks who leads which department, Identity never asks
    what anybody is working on. `.importlinter` grants exactly one exception, for
    `app.contexts.work.* -> app.contexts.identity.public`, and the reverse edge must stay
    unreachable — an Identity that imports Work Core is a dependency cycle, and a cycle is how a
    modular monolith stops being modular (ADR-0001).
    """
    for module in _modules(APP / "contexts" / "identity"):
        offending = {i for i in _imports(module) if i.startswith("app.contexts.work")}
        assert not offending, (
            f"{module.relative_to(BACKEND_ROOT)} imports {sorted(offending)}; Identity is upstream "
            "of Work Core and may not depend on it, not even through its public interface"
        )


def test_identity_never_imports_signal() -> None:
    """Identity is upstream of everything, Signal included.

    Capture asks Identity whether a Person exists so it can resolve a participant. Identity has no
    reason to know that Events exist at all, and the day it does is the day the two can no longer be
    reasoned about separately.
    """
    for module in _modules(APP / "contexts" / "identity"):
        offending = {i for i in _imports(module) if i.startswith("app.contexts.signal")}
        assert not offending, (
            f"{module.relative_to(BACKEND_ROOT)} imports {sorted(offending)}; Identity is upstream "
            "of Signal and may not depend on it"
        )


def test_signal_never_imports_the_work_core() -> None:
    """Capture is upstream of the Work Core, not the other way round and not both.

    An Event records that something happened; a Work item is a decision somebody made about it.
    Signal reaching into Work Core would mean capture had opinions about what the observation
    implies, which is extraction's job and does not exist yet — and when it does, it will read
    Events rather than being embedded in them (BR-E-11).
    """
    for module in _modules(APP / "contexts" / "signal"):
        offending = {i for i in _imports(module) if i.startswith("app.contexts.work")}
        assert not offending, (
            f"{module.relative_to(BACKEND_ROOT)} imports {sorted(offending)}; Signal is upstream "
            "of the Work Core"
        )


def test_signal_reaches_identity_only_through_its_published_interface() -> None:
    for module in _modules(APP / "contexts" / "signal"):
        internals = {
            i
            for i in _imports(module)
            if i.startswith("app.contexts.identity")
            and not i.startswith("app.contexts.identity.public")
        }
        assert not internals, (
            f"{module.relative_to(BACKEND_ROOT)} reaches into Identity's internals: "
            f"{sorted(internals)}"
        )


def test_the_cross_context_exception_list_stays_one_directional() -> None:
    """Guards the configuration, not just the code.

    The test above passes if nobody writes the import. This one fails if somebody makes the import
    *permissible*, which is the change that would happen first and go unnoticed longest.
    """
    config = (BACKEND_ROOT / ".importlinter").read_text()
    exceptions = [
        line.strip()
        for line in config.splitlines()
        if "->" in line and not line.strip().startswith("#")
    ]
    # Every entry points at a `public` module and every arrow runs the same way: Identity is
    # upstream of everything, Signal is upstream of the Work Core, and neither of them knows its
    # consumers exist. A new entry belongs here only with an ADR; an `identity -> *` or a
    # `work -> signal` entry is not a new exception, it is a different architecture.
    assert exceptions == [
        "app.contexts.work.* -> app.contexts.identity.public",
        "app.contexts.signal.* -> app.contexts.identity.public",
    ], (
        f"the cross-context exception list changed to {exceptions}; every entry needs an ADR, and "
        "an identity -> work entry needs a different architecture"
    )


def test_contexts_do_not_import_the_web_framework() -> None:
    for module in _modules(APP / "contexts"):
        assert not {i for i in _imports(module) if i.split(".")[0] in {"fastapi", "starlette"}}, (
            f"{module.relative_to(BACKEND_ROOT)} imports the web framework"
        )


def test_routers_are_thin() -> None:
    """Contract §12. A router parses, authenticates, delegates and serializes. Nothing else.

    The precise half of the api-purity boundary. The import-linter contract of the same name runs
    with `allow_indirect_imports`, because a router necessarily calls things that reach the database
    eventually; this walks what each module actually writes down, which is where the rule bites.
    """
    forbidden_prefixes = (
        "sqlalchemy",
        "app.platform.db",
        # Deciding who may do what happens in the application service. A router that reached the
        # policy engine would be a second place where authorization is expressed (contract §3).
        "app.platform.authz.matrix",
        "app.platform.authz.policy",
    )
    for module in _modules(APP / "api"):
        for imported in _imports(module):
            assert not imported.startswith(forbidden_prefixes), (
                f"{module.relative_to(BACKEND_ROOT)} imports {imported}; routers must not touch "
                "the database or the policy tables directly"
            )


def test_routers_reach_contexts_only_through_their_public_interface() -> None:
    """A repository or a query module is a context's internal, and the API layer is outside it.

    Same rule the contexts hold each other to (ADR-0001), applied to the layer above them. Without
    it the fastest route past an application service — and past its authorization and its audit
    entry — is one import away.
    """
    for module in _modules(APP / "api"):
        for imported in _imports(module):
            if not imported.startswith("app.contexts."):
                continue
            assert imported.endswith(".public"), (
                f"{module.relative_to(BACKEND_ROOT)} imports {imported}; the API layer reaches a "
                "context through its public interface or not at all"
            )


def test_only_the_db_module_creates_engines_or_sessions() -> None:
    """One data-access boundary. Contract §13."""
    allowed = {APP / "platform" / "db.py"}
    for module in _modules(APP):
        if module in allowed:
            continue
        source = module.read_text()
        for marker in ("create_engine(", "sessionmaker("):
            assert marker not in source, (
                f"{module.relative_to(BACKEND_ROOT)} calls {marker}; "
                "engines and sessions come from app.platform.db"
            )
