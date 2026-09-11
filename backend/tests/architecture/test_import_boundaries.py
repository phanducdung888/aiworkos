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


def test_contexts_do_not_import_the_web_framework() -> None:
    for module in _modules(APP / "contexts"):
        assert not {i for i in _imports(module) if i.split(".")[0] in {"fastapi", "starlette"}}, (
            f"{module.relative_to(BACKEND_ROOT)} imports the web framework"
        )


def test_routers_are_thin() -> None:
    """Contract §12. A router parses, authenticates, delegates and serializes. Nothing else."""
    forbidden_prefixes = ("sqlalchemy", "app.platform.db", "app.platform.authz.matrix")
    for module in _modules(APP / "api"):
        for imported in _imports(module):
            assert not imported.startswith(forbidden_prefixes), (
                f"{module.relative_to(BACKEND_ROOT)} imports {imported}; routers must not touch "
                "the database or the policy tables directly"
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
