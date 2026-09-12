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


#: ADR-0040. The order facts flow: something is observed, somebody decides what to do about it,
#: somebody promises it, and a proposal to change any of those is reviewed and executed last.
CONTEXT_ORDER: tuple[str, ...] = (
    "identity",
    "signal",
    "work",
    "commitment",
    "intelligence",
)

#: ADR-0045. What the agent layer may never name directly.
#:
#: The import-linter contract of the same name runs with `allow_indirect_imports`, because the agent
#: necessarily calls published services that reach the database eventually. This is the precise
#: half: what each module actually writes down.
AGENT_FORBIDDEN = (
    "sqlalchemy",
    "psycopg",
    "app.platform.db",
    "app.platform.jobs",
    "app.platform.audit",
    "app.platform.outbox",
)


def _declared_edges() -> list[tuple[str, str]]:
    """Every cross-context exception in `.importlinter`, as (importer, imported)."""
    config = (BACKEND_ROOT / ".importlinter").read_text()
    edges = []
    for line in config.splitlines():
        stripped = line.strip()
        if "->" not in stripped or stripped.startswith("#"):
            continue
        source, _, target = stripped.partition("->")
        edges.append((source.strip(), target.strip()))
    return edges


def test_context_dependencies_form_a_dag() -> None:
    """Guards the configuration, not just the code (ADR-0040).

    The test below this one passes if nobody writes a backwards import. This one fails if somebody
    makes one *permissible*, which is the change that would happen first and go unnoticed longest.

    The order is recomputed from the declaration rather than compared against a fixed list, so
    adding a context means placing it in `CONTEXT_ORDER` — the moment the question is cheapest to
    answer — instead of appending to a list of blessed strings nobody re-reads.
    """
    position = {name: index for index, name in enumerate(CONTEXT_ORDER)}

    for source, target in _declared_edges():
        assert source.startswith("app.contexts."), f"unexpected exception source: {source}"
        importer = source.removeprefix("app.contexts.").split(".")[0]
        assert target.endswith(".public"), (
            f"{source} -> {target} does not go through a published interface; a context's "
            "internals stay unreachable (ADR-0001)"
        )
        imported = target.removeprefix("app.contexts.").split(".")[0]

        assert importer in position, f"{importer} is not placed in CONTEXT_ORDER"
        assert imported in position, f"{imported} is not placed in CONTEXT_ORDER"
        assert position[imported] < position[importer], (
            f"{importer} -> {imported} runs against the context order "
            f"{' -> '.join(CONTEXT_ORDER)}; that is not a new exception, it is a different "
            "architecture (ADR-0040)"
        )


def test_every_declared_edge_is_actually_used() -> None:
    """An exception nobody needs is permission nobody reviewed.

    `.importlinter` does warn about an unused ignore, but a warning in a passing build is a thing
    people stop reading. A dependency that was removed should have its exception removed with it.
    """
    for source, target in _declared_edges():
        importer = source.removeprefix("app.contexts.").split(".")[0]
        used = any(
            target in _imports(module) or target.removesuffix(".public") in _imports(module)
            for module in _modules(APP / "contexts" / importer)
        )
        assert used, f"{source} -> {target} is declared but nothing imports it"


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


def test_the_agent_layer_cannot_reach_the_database() -> None:
    """ADR-0045, the control that makes "AI has no database access" checkable.

    A convention does not stop an import. Neither does a code review six months from now, on a diff
    that does one useful thing and one careless one. This fails in the commit that adds the import.
    """
    for module in _modules(APP / "agent"):
        for imported in _imports(module):
            assert not imported.startswith(AGENT_FORBIDDEN), (
                f"{module.relative_to(BACKEND_ROOT)} imports {imported}; the agent layer reaches "
                "published interfaces and nothing else (ADR-0045)"
            )


def test_the_agent_layer_reaches_contexts_only_through_public_interfaces() -> None:
    for module in _modules(APP / "agent"):
        internals = {
            i
            for i in _imports(module)
            if i.startswith("app.contexts.") and i.split(".")[3:4] != ["public"]
        }
        assert not internals, (
            f"{module.relative_to(BACKEND_ROOT)} reaches into a context's internals: "
            f"{sorted(internals)}"
        )


def test_llm_providers_know_nothing_about_the_domain() -> None:
    """A provider adapter talks to a model and returns structured output.

    It cannot name an Event, a Proposal or a session, which is what makes swapping one a contained
    change rather than an audit of everything it might have touched.
    """
    for module in _modules(APP / "agent" / "providers"):
        offending = {
            i
            for i in _imports(module)
            if i.startswith(("app.contexts", "app.platform", "sqlalchemy", "psycopg"))
        }
        assert not offending, (
            f"{module.relative_to(BACKEND_ROOT)} imports {sorted(offending)}; a provider knows "
            "nothing about the application"
        )


def test_no_request_field_can_claim_ai_origin() -> None:
    """ADR-0043. AI origin is derived from the principal, never asserted by a caller.

    Walks the request schemas rather than trusting that nobody added the field back. The failure
    this prevents is quiet: a Proposal that declares itself human-authored escapes BR-AI-02's
    evidence requirement entirely, and nothing about the response would look wrong.
    """
    schemas = (APP / "api" / "v1" / "schemas.py").read_text()
    for claimed in ("raised_by_ai", "produced_by_ai", "ai_origin", "is_ai"):
        assert claimed not in schemas, (
            f"`{claimed}` appears in the request schemas; AI origin must come from the "
            "authenticated principal (ADR-0043)"
        )


def test_the_worker_dispatches_only_registered_handlers() -> None:
    """A queue that dispatches on an arbitrary string runs arbitrary code by writing a row."""
    from app.workers.runner import HANDLERS

    assert HANDLERS, "the worker has no handlers; jobs would be claimed and dropped"
    for kind, handler in HANDLERS.items():
        assert callable(handler), f"{kind} maps to something that is not callable"


def test_no_synchronous_execution_path_remains() -> None:
    """ADR-0048. One way to perform an approved mutation, and it is the queue.

    Two paths is one more than a design wants, and the cost is not theoretical: every future safety
    property — a rate limit, a circuit breaker, an execution window, a kill switch — has to be
    implemented twice, or it is implemented once and bypassable.
    """
    for module in _modules(APP / "api"):
        source = module.read_text()
        assert "/execute" not in source, (
            f"{module.relative_to(BACKEND_ROOT)} publishes an execution endpoint; approved "
            "mutations run through the queue only (ADR-0048)"
        )
        # The service method exists and is called by the worker. A router calling it would be the
        # same bypass wearing a different URL.
        assert ".execute(" not in source, (
            f"{module.relative_to(BACKEND_ROOT)} calls execute() directly; the worker does that"
        )


def test_the_agent_layer_cannot_reach_the_capability_policy() -> None:
    """ADR-0047. An agent that could widen its own policy makes every other control advisory."""
    for module in _modules(APP / "agent"):
        for imported in _imports(module):
            assert "policy" not in imported.rsplit(".", 1)[-1], (
                f"{module.relative_to(BACKEND_ROOT)} imports {imported}; the agent layer may not "
                "reach the capability policy"
            )


def test_the_worker_cannot_execute_an_unregistered_tool() -> None:
    """The worker dispatches on a closed map, and every handler it can reach ends in the Gateway.

    A queue that dispatches on an arbitrary string runs arbitrary code by writing a row, and a
    handler that bypassed the registry would make the Tool Gateway advisory for exactly the path
    that matters most — the one nobody is watching.
    """
    from app.contexts.intelligence.public import REGISTRY
    from app.workers.runner import HANDLERS

    assert set(HANDLERS) == {"execute_approval"}, (
        f"the worker dispatches {sorted(HANDLERS)}; each kind is a new way to run code"
    )
    forbidden = ("delete", "remove", "purge", "grant", "revoke", "member", "role", "send", "sql")
    for (name, _), tool in REGISTRY.items():
        for word in forbidden:
            assert word not in name, f"{name} looks like a forbidden operation"
            assert word not in tool.run.__name__, f"{tool.run.__name__} is suspicious"


def test_policy_evaluation_cannot_reach_the_database() -> None:
    """The intersection is a pure function over rows that were already loaded.

    Evaluation that could query would be evaluation that could be made to answer differently by
    something other than the policy — and it would run inside authorization, where a surprise query
    is hardest to reason about.
    """
    agent_module = APP / "platform" / "authz" / "agent.py"
    for imported in _imports(agent_module):
        assert not imported.startswith(("sqlalchemy", "app.platform.db", "app.contexts")), (
            f"app/platform/authz/agent.py imports {imported}; policy evaluation is a pure "
            "function and must stay one"
        )
