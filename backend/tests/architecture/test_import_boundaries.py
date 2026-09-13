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
        if target.startswith("app.platform."):
            # A layer edge, not a context edge. `app.platform.agentkit` is shared vocabulary below
            # everything, so importing it runs down the layers and has nothing to do with the
            # order contexts sit in.
            continue
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
        if target.startswith("app.platform."):
            root = source.removeprefix("app.").split(".")[0]
            assert any(
                target.removesuffix(".*") in _imports(module)
                or target.rsplit(".", 1)[0] in _imports(module)
                for module in _modules(APP / root)
            ), f"{source} -> {target} is declared but nothing imports it"
            continue
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


#: The one thing a provider may import from `platform`: shared value types (ADR-0052).
#:
#: `app.platform.agentkit` is pure data — confidence and intent vocabulary that both an agent and
#: WorkOS speak, sitting below both because it belongs to neither. A provider expressing a
#: confidence is not a provider reaching into the application, and naming the exception keeps
#: everything else under `platform` refused outright.
PROVIDER_ALLOWED_PLATFORM = ("app.platform.agentkit",)


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
            and not i.startswith(PROVIDER_ALLOWED_PLATFORM)
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

    # A closed list, written out. Adding a kind is then a deliberate act with a diff on this line,
    # which is the whole mechanism: `analyze_event` joined it in CP26 and had to be argued for.
    assert set(HANDLERS) == {"execute_approval", "analyze_event"}, (
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


def test_the_runtime_names_no_vendor() -> None:
    """ADR-0049. `AgentRuntime` orchestrates; it does not know who answers.

    A runtime that mentioned a vendor would be a runtime that has to change when the vendor does,
    and the provider port would be decoration.
    """
    runtime = (APP / "agent" / "runtime.py").read_text().lower()
    for vendor in ("anthropic", "openai", "gemini", "ollama", "openclaw", "httpx"):
        assert vendor not in runtime, (
            f"app/agent/runtime.py mentions {vendor}; the runtime talks to `LLMProvider` only"
        )


def test_provider_adapters_import_no_application_code() -> None:
    """A provider talks to a model and returns structured output. That is the whole job.

    It cannot read the database, call a service, reach the Tool Gateway or decide an
    authorization — and those are structural facts here rather than instructions in a docstring.
    """
    for module in _modules(APP / "agent" / "providers"):
        offending = {
            i
            for i in _imports(module)
            if i.startswith(("app.contexts", "app.platform", "sqlalchemy", "psycopg"))
            and not i.startswith(PROVIDER_ALLOWED_PLATFORM)
        }
        assert not offending, (
            f"{module.relative_to(BACKEND_ROOT)} imports {sorted(offending)}; a provider knows "
            "nothing about the application (ADR-0049)"
        )


def test_no_credential_is_committed() -> None:
    """The configuration boundary holds no value.

    A default that looked like a key — even a placeholder — is the kind of thing that gets copied
    into an environment file and then into a repository.
    """
    config = (APP / "platform" / "config.py").read_text()
    for line in config.splitlines():
        if "api_key" in line and "=" in line:
            assert '= ""' in line or "str" in line.split("=")[0], (
                f"a credential appears to have a value in config.py: {line.strip()}"
            )


def test_openclaw_is_not_on_any_path() -> None:
    """CP10 is not the OpenClaw integration checkpoint.

    No adapter exists, because the API is not known well enough to write one against — and
    guessing at it would produce a seam shaped by imagination rather than by the thing it has to
    fit (ADR-0049).
    """
    for module in _modules(APP):
        assert "openclaw" not in module.read_text().lower(), (
            f"{module.relative_to(BACKEND_ROOT)} mentions OpenClaw; it is not integrated"
        )


def test_the_execution_deadline_is_derived_in_one_place() -> None:
    """ADR-0051. One constant, one function, no stored column.

    A second definition of the window would be a second answer to "when does this expire", and the
    two would diverge the first time one of them was tuned.
    """
    domain = (APP / "contexts" / "intelligence" / "domain.py").read_text()
    assert domain.count("EXECUTION_WINDOW = ") == 1

    schema = (APP / "contexts" / "intelligence" / "models.py").read_text()
    assert "expires_at" not in schema.split("class ApprovalRecord")[1], (
        "ApprovalRecord holds a stored deadline; it must be derived from `decided_at` (ADR-0051)"
    )


def test_confidence_thresholds_live_in_one_module() -> None:
    """ADR-0050. A band boundary repeated is a threshold nobody can retune safely."""
    for module in _modules(APP):
        if module.name == "confidence.py":
            continue
        source = module.read_text()
        assert "MIN_CONFIDENCE" not in source, (
            f"{module.relative_to(BACKEND_ROOT)} defines its own confidence threshold; the policy "
            "lives in app/agent/providers/confidence.py"
        )


#: Controls that must be *called* somewhere in production code, not merely defined.
#:
#: This list exists because of a specific failure. `assert_within_agent_authority` shipped in
#: Checkpoint 9 holding BR-AI-08 and BR-AI-23, had unit tests proving it behaved correctly, and was
#: invoked by nothing for two checkpoints. Every test passed the whole time. A control that is
#: correct and unreachable is indistinguishable from one that does not exist, and "is it correct"
#: and "is it called" are different questions that need asking separately.
INVOKED_CONTROLS = (
    "assert_within_agent_authority",
    "assert_action_matches",
    "assert_within_execution_window",
    "assert_not_executed",
    "assert_capturable",
    "may_extract",
)


@pytest.mark.parametrize("control", INVOKED_CONTROLS)
def test_every_declared_control_is_actually_invoked(control: str) -> None:
    """A control nobody calls is a comment with a test suite."""
    callers = [
        module.relative_to(BACKEND_ROOT)
        for module in _modules(APP)
        if f"{control}(" in module.read_text()
        and f"def {control}(" not in module.read_text()
    ]
    assert callers, (
        f"{control} is defined and never called; a control that is correct and unreachable is "
        "indistinguishable from one that does not exist"
    )


def test_the_agent_layer_cannot_reach_the_intent_validator() -> None:
    """ADR-0052. The component being constrained must not be able to reach the constraint.

    `IntentValidator` lives in the Intelligence context precisely so that `app/agent` cannot import
    it. An agent that could call its own validator could also decide not to.
    """
    for module in _modules(APP / "agent"):
        for imported in _imports(module):
            assert "intents" not in imported.rsplit(".", 1)[-1], (
                f"{module.relative_to(BACKEND_ROOT)} imports {imported}; the validator is not the "
                "agent's to reach"
            )


def _referenced_names(module: Path) -> set[str]:
    """Every identifier a module actually uses, from the AST.

    Not a text search: a docstring explaining that the runtime no longer calls `raise_proposal` is
    prose about the constraint, and a test that cannot tell it from a call would punish the comment
    that documents the rule.
    """
    tree = ast.parse(module.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_the_agent_layer_cannot_raise_a_proposal_directly() -> None:
    """An agent emits intents. Turning one into a Proposal is WorkOS's decision (ADR-0052)."""
    forbidden = {"RaiseProposal", "raise_proposal", "CreateEvidence", "EvidenceService"}
    for module in _modules(APP / "agent"):
        used = _referenced_names(module) & forbidden
        assert not used, (
            f"{module.relative_to(BACKEND_ROOT)} uses {sorted(used)}; an agent produces "
            "ToolIntents and nothing else"
        )


def test_the_runtime_satisfies_the_agent_contract() -> None:
    """ADR-0052. The in-house runtime is an implementation of the boundary, not an exception to it.

    Checkpoint 11 declared `AgentContract` as what an external agent would implement and left
    nothing implementing it — including the runtime this repository ships. A protocol nobody
    satisfies is a shape nobody has checked, and the first implementation would have discovered
    whatever was wrong with it.
    """
    import inspect

    from app.agent.runtime import AgentRuntime
    from app.platform.agentkit import AgentContract

    for name, expected in inspect.getmembers(
        AgentContract, predicate=inspect.isfunction
    ):
        if name.startswith("_"):
            continue
        actual = getattr(AgentRuntime, name, None)
        assert actual is not None, f"AgentRuntime does not implement {name}"
        assert inspect.signature(actual).parameters.keys() == (
            inspect.signature(expected).parameters.keys()
        ), f"AgentRuntime.{name} does not match the contract's signature"

    assert isinstance(getattr(AgentRuntime, "name", None), str), (
        "AgentRuntime must expose a `name` for `ai_interaction.runtime`"
    )


def test_smoke_tests_are_skipped_without_an_explicit_opt_in() -> None:
    """CI stays offline. A smoke test that runs accidentally bills somebody and fails a build for
    reasons nobody can reproduce."""
    source = (BACKEND_ROOT / "tests" / "smoke" / "test_real_provider.py").read_text()
    assert "RUN_REAL_PROVIDER_TESTS" in source
    assert "skipif" in source
    assert 'os.environ.get("ANTHROPIC_API_KEY", "")' in source, (
        "the credential must come from the environment and have no default"
    )


def test_no_credential_appears_in_the_repository() -> None:
    """A key committed once is a key that has to be rotated, found in a mirror, and explained."""
    import re

    pattern = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")
    for path in list(APP.rglob("*.py")) + list((BACKEND_ROOT / "tests").rglob("*.py")):
        assert not pattern.search(path.read_text()), (
            f"{path.relative_to(BACKEND_ROOT)} appears to contain an API key"
        )
def _calls_inside(module: Path, function: str) -> set[str]:
    """Every function or method *called* inside a named function, however deeply nested.

    The AST again rather than a text search, for the reason `_referenced_names` gives: a docstring
    that names the rule is prose about the rule, and a test that cannot tell prose from a call
    rewards deleting the comment.
    """
    tree = ast.parse(module.read_text(), filename=str(module))
    target: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            target = node
            break
    assert target is not None, f"{module.name} has no function named {function}"
    called: set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    return called


def test_may_attribute_is_invoked_on_the_resolution_path() -> None:
    """BR-I-06 and ADR-0054 — the regression this repository keeps producing.

    `may_attribute` was written in Checkpoint 5, unit-tested, exported, and never called by
    anything for nine checkpoints. A rule that no path reaches is documentation, and the tests that
    covered it proved only that the documentation was internally consistent.

    So this asserts the *path*, link by link: capture resolves its participants, resolution asks
    Identity, and Identity asks the rule. Breaking any link fails here rather than silently
    reverting attribution to "whatever the caller claimed".
    """
    identity_queries = APP / "contexts" / "identity" / "queries.py"
    assert "may_attribute" in _calls_inside(identity_queries, "resolve_attribution"), (
        "resolve_attribution decides attribution without asking may_attribute (BR-I-06)"
    )

    signal_services = APP / "contexts" / "signal" / "services.py"
    assert "resolve_attribution" in _calls_inside(signal_services, "_resolve_participants"), (
        "participant resolution does not go through Identity's published decision (ADR-0054)"
    )
    assert "_resolve_participants" in _calls_inside(signal_services, "capture"), (
        "capture persists participants without resolving them; the rule is off the path again"
    )


def test_identity_resolution_never_creates_anything() -> None:
    """ADR-0054: lookup-only. A resolver that can write is a resolver that can vouch for itself."""
    forbidden = {
        "insert_external_identity",
        "update_external_identity",
        "CreateExternalIdentity",
        "CreatePerson",
        "ExternalIdentityService",
        "PersonService",
    }
    for module, function in (
        (APP / "contexts" / "identity" / "queries.py", "resolve_attribution"),
        (APP / "contexts" / "signal" / "services.py", "_resolve_participants"),
    ):
        used = _calls_inside(module, function) & forbidden
        assert not used, (
            f"{module.name}:{function} calls {sorted(used)}; resolution reads and never creates"
        )


def test_the_agent_layer_cannot_name_a_committer_or_resolve_an_identity() -> None:
    """ADR-0054 restated where it can fail. The LLM never sees or selects a `person_id`.

    `routed_to_person_id` and `principal.person_id` are deliberately absent from this list: both are
    the *delegating human*, which the runtime must know to record who asked. What it may never do
    is name the person a promise is attributed to, or reach the mapping that would tell it.
    """
    forbidden = {
        "committed_by_person_id",
        "resolve_attribution",
        "AttributedIdentity",
        "ExternalIdentity",
        "may_attribute",
    }
    for module in _modules(APP / "agent"):
        used = _referenced_names(module) & forbidden
        assert not used, (
            f"{module.relative_to(BACKEND_ROOT)} uses {sorted(used)}; attribution is decided in "
            "WorkOS and never by the agent (BR-AI-34, ADR-0054)"
        )


def test_a_commitment_is_never_deduplicated_against_work_titles() -> None:
    """BR-AI-05 asks whether *this* already exists, and a promise is not a Work item.

    Until CP14 every accepted intent was checked against Work titles, commitments included. The
    defect was invisible because the check "passed": it returned a list, the audit row said a
    search happened, and nothing recorded that the wrong corpus had been searched.
    """
    # In `workers/analysis.py` since CP26, where a request and a queued job share one code path.
    # The rule is about the routing, not about the file it lives in.
    orchestration = APP / "workers" / "analysis.py"
    routed = _calls_inside(orchestration, "_duplicate_search")
    assert "find_similar_commitments" in routed, (
        "no commitment-scoped duplicate search on the routing path"
    )
    assert "find_similar_work" in routed, "Work intents lost their duplicate search"

    source = orchestration.read_text()
    body = source[source.index("def submit_analysis") : source.index("def _duplicate_search")]
    assert "find_similar_work" not in body, (
        "submit_analysis still searches Work directly; the corpus must be chosen by intent kind"
    )


def test_the_application_never_imports_a_connector() -> None:
    """CP20, ADR-0058. The dependency runs one way and only one way.

    A connector imports nothing from WorkOS — it posts to the capture API — and WorkOS imports
    nothing from a connector. The backend *test* suite does import the normaliser, deliberately, so
    the end-to-end journey exercises the connector's own code instead of re-implementing it; that
    is a test reaching outwards, which is the opposite direction and carries none of the coupling.

    An application that imported a connector would have a vendor's protocol inside the process that
    holds the database credential, which is the arrangement ADR-0027 put behind a separate identity
    to avoid.
    """
    for module in _modules(APP):
        for imported in _imports(module):
            assert not imported.startswith("connectors"), (
                f"{module.relative_to(BACKEND_ROOT)} imports {imported}; a connector runs outside "
                "this process and is not the application's to reach"
            )


def test_a_connector_cannot_reach_the_database_or_the_domain() -> None:
    """ADR-0058: receiving and normalising, and nothing else.

    Asserted over the connector's own source rather than trusted. A connector that could import a
    context would be a second write path into Signal — and one that could import a session would be
    holding the credential the whole arrangement exists to keep away from vendor code.
    """
    connectors = BACKEND_ROOT.parent / "connectors"
    forbidden = ("app.", "sqlalchemy", "alembic", "psycopg", "fastapi")
    for module in _modules(connectors):
        if "tests" in module.parts:
            continue
        for imported in _imports(module):
            assert not imported.startswith(forbidden), (
                f"{module.name} imports {imported}; a connector normalises and posts, and holds "
                "no database credential and no business rule"
            )


def test_the_connector_holds_no_credential_of_its_own() -> None:
    """Everything comes from the environment. A default here would be a secret in a repository."""
    import re

    connectors = BACKEND_ROOT.parent / "connectors"
    suspicious = re.compile(r"(?i)(password|token|secret)\s*[:=]\s*[\"'][^\"']{6,}")
    for module in _modules(connectors):
        if "tests" in module.parts:
            continue
        found = suspicious.search(module.read_text())
        assert found is None, f"{module.name} appears to hold a literal credential: {found}"
