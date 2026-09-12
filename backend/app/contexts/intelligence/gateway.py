"""The Tool Gateway (ADR-0042, ADR-0002, BR-AI-16).

A closed registry. Each entry maps a tool name and version to one function that calls exactly one
existing application service. There is no generic executor, no field-path applier, no SQL, and no
way to name an operation the registry does not contain — an unknown tool is a refusal, not a
fallback.

Two properties are worth stating because they are easy to erode.

*Every tool calls a service; none reimplements one.* Creating Work here runs the same
`WorkService.create` a human request runs, so the BR-W rules, reference validation, audit and the
outbox all happen because they already happen there. A tool that assembled its own insert would be
a second implementation of the rules, drifting from the first the moment either changed.

*The forbidden operations have no entry at all.* Deletion, cancellation, membership, roles and
outbound messaging (BR-AI-06, BR-AI-23, BR-AI-24) are absent rather than disabled, so there is no
flag to get wrong and nothing to switch on by accident. A Proposal naming one is refused when it is
created, because the tool does not exist.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

import app.contexts.commitment.public as commitment
import app.contexts.work.public as work
from app.contexts.intelligence.action import Action
from app.platform.actor import Actor
from app.platform.authz import Principal
from app.platform.errors import DomainRuleViolation


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionContext:
    """What a tool needs, and deliberately nothing more.

    No raw connection and no engine: a tool receives the same session the request is running in, so
    its writes are part of the caller's transaction and commit with the ApprovalRecord or not at all
    (BR-PR-01).
    """

    session: Session
    #: The *approver's* principal. Execution is attributed to them and authorized as them, so
    #: approving never grants permission they lack (BR-PR-05, BR-AI-19).
    principal: Principal
    actor: Actor


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionResult:
    entity_type: str
    entity_id: uuid.UUID


ToolFn = Callable[[ExecutionContext, dict[str, Any]], ExecutionResult]


@dataclasses.dataclass(frozen=True, slots=True)
class Tool:
    name: str
    version: str
    #: What the Proposal's `target_type` must be for this tool. Checked at proposal time, so a
    #: mismatch is caught by the person writing the Proposal rather than at execution.
    target_type: str
    required_arguments: frozenset[str]
    optional_arguments: frozenset[str]
    run: ToolFn

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.version)


def _require(arguments: dict[str, Any], name: str) -> Any:
    if name not in arguments or arguments[name] is None:
        raise DomainRuleViolation("BR-AI-17", f"the action is missing {name}")
    return arguments[name]


def _as_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise DomainRuleViolation("BR-AI-17", f"{field} is not an identifier") from error


def _as_date(value: Any, field: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as error:
        raise DomainRuleViolation("BR-AI-17", f"{field} is not a date") from error


# --------------------------------------------------------------------------- tools


def _create_work(context: ExecutionContext, arguments: dict[str, Any]) -> ExecutionResult:
    created = work.WorkService(
        work.ServiceContext(
            session=context.session, principal=context.principal, actor=context.actor
        )
    ).create(
        work.CreateWork(
            title=_require(arguments, "title"),
            description=arguments.get("description"),
            project_id=(
                _as_uuid(arguments["project_id"], "project_id")
                if arguments.get("project_id")
                else None
            ),
            due_date=_as_date(arguments.get("due_date"), "due_date"),
        )
    )
    return ExecutionResult(entity_type="work", entity_id=created.id)


def _update_work_status(
    context: ExecutionContext, arguments: dict[str, Any]
) -> ExecutionResult:
    work_id = _as_uuid(_require(arguments, "work_id"), "work_id")
    updated = work.WorkService(
        work.ServiceContext(
            session=context.session, principal=context.principal, actor=context.actor
        )
    ).change_status(
        work.ChangeWorkStatus(
            work_id=work_id,
            expected_version=int(_require(arguments, "expected_version")),
            target=work.WorkStatus(_require(arguments, "target")),
        )
    )
    return ExecutionResult(entity_type="work", entity_id=updated.id)


def _assign_work(context: ExecutionContext, arguments: dict[str, Any]) -> ExecutionResult:
    assignment = work.AssignmentService(
        work.ServiceContext(
            session=context.session, principal=context.principal, actor=context.actor
        )
    ).assign(
        work.AssignWork(
            work_id=_as_uuid(_require(arguments, "work_id"), "work_id"),
            person_id=_as_uuid(_require(arguments, "person_id"), "person_id"),
            role=work.AssignmentRole(arguments.get("role", "CONTRIBUTOR")),
        )
    )
    return ExecutionResult(entity_type="work_assignment", entity_id=assignment.id)


def _create_commitment(
    context: ExecutionContext, arguments: dict[str, Any]
) -> ExecutionResult:
    created = commitment.CommitmentService(
        commitment.ServiceContext(
            session=context.session, principal=context.principal, actor=context.actor
        )
    ).create(
        commitment.CreateCommitment(
            statement=_require(arguments, "statement"),
            committed_by_person_id=_as_uuid(
                _require(arguments, "committed_by_person_id"), "committed_by_person_id"
            ),
            committed_to_person_id=(
                _as_uuid(arguments["committed_to_person_id"], "committed_to_person_id")
                if arguments.get("committed_to_person_id")
                else None
            ),
            due_date=_as_date(arguments.get("due_date"), "due_date"),
            due_precision=commitment.DuePrecision(arguments.get("due_precision", "vague")),
            origin_event_id=(
                _as_uuid(arguments["origin_event_id"], "origin_event_id")
                if arguments.get("origin_event_id")
                else None
            ),
            confidence=int(arguments.get("confidence", 0)),
            # BR-C-03. Anything arriving through the Gateway came from a Proposal, and a Proposal
            # carries its Evidence — so the commitment is required to cite it.
            produced_by_ai=True,
            evidence_ids=tuple(
                _as_uuid(value, "evidence_ids") for value in arguments.get("evidence_ids", [])
            ),
        )
    )
    return ExecutionResult(entity_type="commitment", entity_id=created.id)


def _change_commitment_status(
    context: ExecutionContext, arguments: dict[str, Any]
) -> ExecutionResult:
    updated = commitment.CommitmentService(
        commitment.ServiceContext(
            session=context.session,
            principal=context.principal,
            actor=context.actor,
            leads_team_ids=frozenset(),
        )
    ).change_status(
        commitment.ChangeCommitmentStatus(
            commitment_id=_as_uuid(_require(arguments, "commitment_id"), "commitment_id"),
            expected_version=int(_require(arguments, "expected_version")),
            target=commitment.CommitmentStatus(_require(arguments, "target")),
            new_due_date=_as_date(arguments.get("new_due_date"), "new_due_date"),
            # BR-C-10: fulfilment through this path is authorised by the approval that produced it,
            # and the executor passes the record id so the domain can see one exists.
            approval_record_id=(
                _as_uuid(arguments["approval_record_id"], "approval_record_id")
                if arguments.get("approval_record_id")
                else None
            ),
            produced_by_ai=True,
        )
    )
    return ExecutionResult(entity_type="commitment", entity_id=updated.id)


#: The complete set of things an approved Proposal can ever do.
#:
#: Readable in one screen on purpose (ADR-0042). Adding a capability is a registry entry, an
#: argument schema, a matrix row if a new resource is involved, and a test — never a payload that
#: happens to reach further.
REGISTRY: dict[tuple[str, str], Tool] = {
    tool.key: tool
    for tool in (
        Tool(
            name="create_work",
            version="v1",
            target_type="work",
            required_arguments=frozenset({"title"}),
            optional_arguments=frozenset({"description", "project_id", "due_date"}),
            run=_create_work,
        ),
        Tool(
            name="update_work_status",
            version="v1",
            target_type="work",
            required_arguments=frozenset({"work_id", "expected_version", "target"}),
            optional_arguments=frozenset(),
            run=_update_work_status,
        ),
        Tool(
            name="assign_work",
            version="v1",
            target_type="work_assignment",
            required_arguments=frozenset({"work_id", "person_id"}),
            optional_arguments=frozenset({"role"}),
            run=_assign_work,
        ),
        Tool(
            name="create_commitment",
            version="v1",
            target_type="commitment",
            required_arguments=frozenset({"statement", "committed_by_person_id"}),
            optional_arguments=frozenset(
                {
                    "committed_to_person_id",
                    "due_date",
                    "due_precision",
                    "origin_event_id",
                    "confidence",
                    "evidence_ids",
                }
            ),
            run=_create_commitment,
        ),
        Tool(
            name="change_commitment_status",
            version="v1",
            target_type="commitment",
            required_arguments=frozenset({"commitment_id", "expected_version", "target"}),
            optional_arguments=frozenset({"new_due_date", "approval_record_id"}),
            run=_change_commitment_status,
        ),
    )
}


def resolve(action: Action) -> Tool:
    """The registered tool for this action, or a refusal.

    An unknown name and an unknown version are the same answer. Version is part of the key because
    an action approved against `v1` must not silently execute against a `v2` that means something
    different — the approval was for the behaviour that existed when it was given.
    """
    tool = REGISTRY.get((action.tool, action.tool_version))
    if tool is None:
        raise DomainRuleViolation(
            "BR-AI-16",
            f"no tool named {action.tool}/{action.tool_version} exists; "
            "the gateway executes registered tools only",
        )
    return tool


def validate_arguments(tool: Tool, arguments: dict[str, Any]) -> None:
    """Checked when a Proposal is created, so a malformed action never reaches approval.

    Unknown arguments are refused rather than ignored. An argument the executor silently drops is a
    difference between what a reviewer read and what would run, which is the whole class of problem
    the action hash exists to prevent.
    """
    missing = tool.required_arguments - arguments.keys()
    if missing:
        raise DomainRuleViolation(
            "BR-AI-17", f"the action is missing {', '.join(sorted(missing))}"
        )
    unknown = arguments.keys() - (tool.required_arguments | tool.optional_arguments)
    if unknown:
        raise DomainRuleViolation(
            "BR-AI-17", f"the action carries unknown arguments: {', '.join(sorted(unknown))}"
        )


def execute(context: ExecutionContext, action: Action) -> ExecutionResult:
    """Run one registered tool. The caller has already verified the approval (ADR-0041)."""
    tool = resolve(action)
    validate_arguments(tool, action.arguments)
    return tool.run(context, action.arguments)
