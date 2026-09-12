"""Proposal, approval and execution.

The three are deliberately three separate acts, and the seams between them are where the safety
lives.

*Raising* records a reviewable action and its hash. Nothing has happened yet.

*Deciding* writes an immutable ApprovalRecord binding one person's authority to one exact action
(BR-PR-01). Still nothing has happened to Work or to a Commitment — approving is not executing, and
keeping them apart is what makes "approve now, execute when the queue drains" a safe thing to build
later rather than a refactor.

*Executing* recomputes the hash, refuses any mismatch (BR-AI-18), claims the record exactly once,
and calls one registered tool — which calls one existing application service, which authorizes the
approver normally. Approving therefore never grants permission the approver lacks (BR-PR-05): if
they could not have made the change themselves, the service refuses it here too.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

import app.contexts.identity.public as identity
import app.contexts.signal.public as signal
from app.contexts.intelligence import gateway, repository
from app.contexts.intelligence.action import Action, hash_of
from app.contexts.intelligence.commands import (
    DecideProposal,
    ExecuteApproval,
    RaiseProposal,
    ReviseProposal,
)
from app.contexts.intelligence.domain import (
    EXECUTION_WINDOW,
    Decision,
    ExecutionStatus,
    ProposalStatus,
    ProposalView,
    assert_action_matches,
    assert_decidable,
    assert_may_decide,
    assert_not_executed,
    assert_within_execution_window,
    execution_deadline,
    expiry_from,
    status_for,
    validate_creation,
)
from app.contexts.intelligence.models import ApprovalRecord, Proposal
from app.platform import jobs
from app.platform.actor import Actor, ActorType
from app.platform.audit import record_audit
from app.platform.authz import (
    Action as AuthzAction,
)
from app.platform.authz import (
    Decision as AuthzDecision,
)
from app.platform.authz import (
    Principal,
    Relation,
    ResourceType,
    Role,
    authorize,
)
from app.platform.authz.model import ResourceRef
from app.platform.errors import (
    DomainRuleViolation,
    EntityNotFound,
    TerminalJobError,
)
from app.platform.outbox import append_domain_event
from app.platform.principal import resolve_principal_for


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceContext:
    session: Session
    principal: Principal
    actor: Actor


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    approval: ApprovalRecord
    entity_type: str
    entity_id: uuid.UUID


def _ref(
    org_id: uuid.UUID, relations: frozenset[Relation], resource_id: uuid.UUID | None = None
) -> ResourceRef:
    return ResourceRef(
        type=ResourceType.PROPOSAL, org_id=org_id, id=resource_id, relations=relations
    )


def _view(proposal: Proposal) -> ProposalView:
    return ProposalView(
        id=proposal.id,
        status=ProposalStatus(proposal.status),
        action_hash=proposal.action_hash,
        routed_to_person_id=proposal.routed_to_person_id,
        expires_at=proposal.expires_at,
        raised_by_person_id=proposal.raised_by_person_id,
    )


def _snapshot(proposal: Proposal) -> dict[str, Any]:
    """What audit records about a Proposal.

    The action and its hash are included: they are the whole substance of what was proposed, and an
    audit trail that omitted them could not show what somebody approved. No Event body is copied —
    the Proposal cites its Evidence, and the Evidence cites its Event.
    """
    return {
        "id": str(proposal.id),
        "kind": proposal.kind,
        "target_type": proposal.target_type,
        "target_id": str(proposal.target_id) if proposal.target_id else None,
        "summary": proposal.summary,
        "confidence": proposal.confidence,
        "action": proposal.action,
        "action_hash": proposal.action_hash,
        "status": proposal.status,
        "routed_to_person_id": str(proposal.routed_to_person_id),
        "source_event_id": (
            str(proposal.source_event_id) if proposal.source_event_id else None
        ),
        "supersedes_proposal_id": (
            str(proposal.supersedes_proposal_id) if proposal.supersedes_proposal_id else None
        ),
    }


class ProposalService:
    def __init__(self, context: ServiceContext) -> None:
        self._ctx = context

    @property
    def _session(self) -> Session:
        return self._ctx.session

    @property
    def _org_id(self) -> uuid.UUID:
        return self._ctx.principal.org_id

    @property
    def _is_ai_actor(self) -> bool:
        """ADR-0043. AI origin is read from the actor, never from a request field.

        The test is `ai_interaction_id`, not `type`. Both cases matter and only one of them is an
        AI-typed actor:

        * an agent raising a Proposal is `ActorType.AI` and carries its interaction;
        * an approved AI Proposal *executing* is attributed to the approving **person**
          (BR-AI-19) and still carries the interaction that produced it.

        The second is the one that decides BR-C-03: a Commitment the AI thought of does not stop
        being AI-sourced because a human approved it, so the evidence requirement follows the
        interaction rather than the signature on the execution.
        """
        return self._ctx.actor.ai_interaction_id is not None

    # ------------------------------------------------------------------ raise

    def raise_proposal(self, command: RaiseProposal) -> Proposal:
        decision = self._authorize(AuthzAction.CREATE, frozenset())
        validate_creation(
            kind=command.kind,
            target_type=command.target_type,
            target_id=command.target_id,
            summary=command.summary,
            confidence=command.confidence,
            evidence_count=len(command.evidence_ids),
            raised_by_ai=self._is_ai_actor,
        )

        # The tool has to exist and the arguments have to fit it *before* anybody reviews. A
        # Proposal that could never execute is worse than no Proposal: somebody reads it, approves
        # it, and discovers at execution that it was never runnable.
        action = Action(
            tool=command.tool, tool_version=command.tool_version, arguments=command.arguments
        )
        tool = gateway.resolve(action)
        gateway.validate_arguments(tool, command.arguments)
        if tool.target_type != command.target_type:
            raise DomainRuleViolation(
                "BR-PR-01",
                f"{command.tool} produces a {tool.target_type}, not a {command.target_type}",
            )

        self._assert_references(command)

        now = dt.datetime.now(dt.UTC)
        proposal = repository.insert_proposal(
            self._session,
            org_id=self._org_id,
            kind=command.kind.value,
            target_type=command.target_type,
            target_id=command.target_id,
            summary=command.summary.strip(),
            reason=command.reason,
            confidence=command.confidence,
            action=action.as_json(),
            action_hash=action.digest(),
            source_event_id=command.source_event_id,
            supersedes_proposal_id=None,
            raised_by_person_id=self._ctx.actor.person_id,
            routed_to_person_id=command.routed_to_person_id,
            expires_at=expiry_from(now),
            # Taken from the actor rather than the command when the actor is an AI: a run cannot
            # produce a Proposal that forgets to say which run produced it (BR-AI-02).
            ai_interaction_id=command.ai_interaction_id or self._ctx.actor.ai_interaction_id,
        )
        self._attach(proposal, command.evidence_ids, command.changes)
        self._audit(
            AuthzAction.CREATE, proposal.id, None, _snapshot(proposal), decision
        )
        self._emit("ProposalRaised", proposal)
        return proposal

    def revise(self, command: ReviseProposal) -> Proposal:
        """BR-PR-02. The revision is a new Proposal; the original becomes `superseded`.

        Any ApprovalRecord for the original is invalidated by construction rather than by a cleanup
        step: the new action has a new hash, and execution matches on hash (ADR-0041).
        """
        original = self._load(command.proposal_id)
        decision = self._authorize(
            AuthzAction.UPDATE, self._relations(original), original.id
        )
        if ProposalStatus(original.status) is not ProposalStatus.PENDING:
            raise DomainRuleViolation(
                "BR-PR-02", f"a {original.status} proposal cannot be revised"
            )

        stored = Action.from_json(dict(original.action))
        action = Action(
            tool=stored.tool,
            tool_version=stored.tool_version,
            arguments=command.arguments if command.arguments is not None else stored.arguments,
        )
        gateway.validate_arguments(gateway.resolve(action), action.arguments)

        now = dt.datetime.now(dt.UTC)
        replacement = repository.insert_proposal(
            self._session,
            org_id=self._org_id,
            kind=original.kind,
            target_type=original.target_type,
            target_id=original.target_id,
            summary=(command.summary or original.summary).strip(),
            reason=command.reason if command.reason is not None else original.reason,
            confidence=original.confidence,
            action=action.as_json(),
            action_hash=action.digest(),
            source_event_id=original.source_event_id,
            supersedes_proposal_id=original.id,
            raised_by_person_id=self._ctx.actor.person_id,
            routed_to_person_id=original.routed_to_person_id,
            expires_at=expiry_from(now),
        )
        # Provenance carries forward: the revision rests on the same Evidence unless the reviser
        # says otherwise, and dropping it would break BR-PR-08's chain at the revision.
        for evidence_id in repository.evidence_ids_for(
            self._session, org_id=self._org_id, proposal_id=original.id
        ):
            repository.link_evidence(
                self._session,
                org_id=self._org_id,
                proposal_id=replacement.id,
                evidence_id=evidence_id,
            )
        self._record_changes(replacement, command.changes or ())

        repository.decide_proposal(
            self._session,
            org_id=self._org_id,
            proposal_id=original.id,
            expected_version=command.expected_version,
            status=ProposalStatus.SUPERSEDED.value,
            reviewed_by_person_id=None,
            reviewed_at=None,
        )
        self._audit(
            AuthzAction.UPDATE,
            original.id,
            _snapshot(original),
            {"status": ProposalStatus.SUPERSEDED.value, "superseded_by": str(replacement.id)},
            decision,
        )
        self._emit("ProposalSuperseded", replacement)
        return replacement

    # ------------------------------------------------------------------ decide

    def decide(self, command: DecideProposal) -> ApprovalRecord:
        """BR-PR-01. Writes the ApprovalRecord. Executes nothing."""
        proposal = self._load(command.proposal_id)
        authz = self._authorize(
            AuthzAction.APPROVE, self._relations(proposal), proposal.id
        )
        now = dt.datetime.now(dt.UTC)
        view = _view(proposal)
        assert_decidable(view, now=now)
        assert_may_decide(
            view,
            approver_person_id=self._ctx.actor.person_id,
            is_org_admin=Role.ORG_ADMIN in self._ctx.principal.roles,
        )
        approver = self._ctx.actor.person_id
        if approver is None:  # pragma: no cover - assert_may_decide already refused
            raise DomainRuleViolation("BR-PR-01", "a proposal is decided by a person")

        stored = Action.from_json(dict(proposal.action))
        if command.decision is Decision.APPROVED_WITH_EDITS:
            if command.edited_arguments is None:
                raise DomainRuleViolation(
                    "BR-PR-06", "approving with edits requires the edited arguments"
                )
            approved = Action(
                tool=stored.tool,
                tool_version=stored.tool_version,
                arguments=command.edited_arguments,
            )
            gateway.validate_arguments(gateway.resolve(approved), approved.arguments)
            # BR-PR-06: the edit is the signal that matters most for evaluation, so the diff is
            # retained rather than merely the result.
            edits: dict[str, Any] | None = {
                "from": stored.as_json()["arguments"],
                "to": approved.as_json()["arguments"],
            }
        else:
            if command.edited_arguments is not None:
                raise DomainRuleViolation(
                    "BR-PR-06", "edits accompany an approve-with-edits decision only"
                )
            approved, edits = stored, None

        rejected = command.decision is Decision.REJECTED
        record = repository.insert_approval(
            self._session,
            org_id=self._org_id,
            proposal_id=proposal.id,
            approver_person_id=approver,
            decision=command.decision.value,
            approved_action=approved.as_json(),
            approved_action_hash=approved.digest(),
            edits=edits,
            execution_status=(
                ExecutionStatus.NOT_APPLICABLE.value
                if rejected
                else ExecutionStatus.PENDING.value
            ),
        )
        decided = repository.decide_proposal(
            self._session,
            org_id=self._org_id,
            proposal_id=proposal.id,
            expected_version=command.expected_version,
            status=status_for(command.decision).value,
            reviewed_by_person_id=approver,
            reviewed_at=now,
            rejection_reason=command.rejection_reason if rejected else None,
        )
        self._audit(
            AuthzAction.APPROVE,
            proposal.id,
            _snapshot(proposal),
            {
                **_snapshot(decided),
                "approval_record_id": str(record.id),
                "decision": record.decision,
                "approved_action_hash": record.approved_action_hash,
            },
            authz,
        )
        self._emit(
            "ProposalRejected" if rejected else "ProposalApproved",
            decided,
            extra={"approval_record_id": str(record.id)},
        )
        return record

    # ------------------------------------------------------------------ execute

    def execute(self, command: ExecuteApproval) -> ExecutionOutcome:
        """BR-AI-18, BR-AI-19. Recompute, claim once, call one registered tool."""
        record = repository.get_approval(
            self._session, org_id=self._org_id, approval_id=command.approval_id
        )
        if record is None:
            raise EntityNotFound("approval_record", command.approval_id)
        assert_not_executed(ExecutionStatus(record.execution_status))

        # The two independent things that must agree (ADR-0041): the bytes stored as approved, and
        # the digest taken when they were approved.
        assert_action_matches(
            approved_hash=record.approved_action_hash,
            recomputed_hash=hash_of(dict(record.approved_action)),
        )
        # BR-AI-22, checked here so the refusal is explicable, and again inside the claim so it
        # is atomic (ADR-0051). A worker that passes this line and is descheduled past the
        # deadline still fails the claim, because the deadline is part of that statement.
        assert_within_execution_window(
            decided_at=record.decided_at, now=dt.datetime.now(dt.UTC)
        )
        if not repository.claim_for_execution(
            self._session,
            org_id=self._org_id,
            approval_id=record.id,
            window=EXECUTION_WINDOW,
        ):
            # The claim failed for one of two reasons and the caller deserves to know which: the
            # approval was spent while we were reading it, or it expired in the same window.
            assert_within_execution_window(
                decided_at=record.decided_at, now=dt.datetime.now(dt.UTC)
            )
            raise DomainRuleViolation(
                "BR-PR-01", "this approval is already being executed"
            )

        action = Action.from_json(dict(record.approved_action))
        proposal = self._load(record.proposal_id)
        # BR-AI-19. Attributed to the approving Person, carrying the chain that authorised it, so
        # the resulting audit entry names the Proposal and the ApprovalRecord without a join. The
        # interaction comes along too: the mutation is a person's act, and its *idea* is still the
        # AI's, which is what BR-C-03 and BR-AI-02 ask about downstream.
        executing_actor = dataclasses.replace(
            self._ctx.actor,
            type=ActorType.PERSON,
            person_id=record.approver_person_id,
            ai_interaction_id=proposal.ai_interaction_id,
            extra={
                **self._ctx.actor.extra,
                "executed_via": "ai_tool",
                "proposal_id": str(record.proposal_id),
                "approval_record_id": str(record.id),
            },
        )
        result = gateway.execute(
            gateway.ExecutionContext(
                session=self._session,
                principal=self._ctx.principal,
                actor=executing_actor,
            ),
            action,
        )
        updated = repository.record_execution(
            self._session,
            org_id=self._org_id,
            approval_id=record.id,
            execution_status=ExecutionStatus.EXECUTED.value,
            resulting_entity_type=result.entity_type,
            resulting_entity_id=result.entity_id,
        )
        self._emit_execution(updated, result)
        return ExecutionOutcome(
            approval=updated, entity_type=result.entity_type, entity_id=result.entity_id
        )

    # ------------------------------------------------------------------ internals

    def _assert_references(self, command: RaiseProposal) -> None:
        identity.assert_person_exists(
            self._session,
            org_id=self._org_id,
            person_id=command.routed_to_person_id,
            field="routed_to_person_id",
        )
        if command.source_event_id is not None:
            signal.assert_event_exists(
                self._session,
                org_id=self._org_id,
                event_id=command.source_event_id,
                field="source_event_id",
            )
        for index, evidence_id in enumerate(command.evidence_ids):
            signal.assert_evidence_exists(
                self._session,
                org_id=self._org_id,
                evidence_id=evidence_id,
                field=f"evidence_ids[{index}]",
            )

    def _attach(
        self,
        proposal: Proposal,
        evidence_ids: tuple[uuid.UUID, ...],
        changes: tuple[Any, ...],
    ) -> None:
        for evidence_id in evidence_ids:
            repository.link_evidence(
                self._session,
                org_id=self._org_id,
                proposal_id=proposal.id,
                evidence_id=evidence_id,
            )
        self._record_changes(proposal, changes)

    def _record_changes(self, proposal: Proposal, changes: tuple[Any, ...]) -> None:
        for change in changes:
            repository.add_change(
                self._session,
                org_id=self._org_id,
                proposal_id=proposal.id,
                field_path=change.field_path,
                current_value=change.current_value,
                proposed_value=change.proposed_value,
            )

    def _relations(self, proposal: Proposal) -> frozenset[Relation]:
        """The person a Proposal was routed to has a personal relation to it (BR-PR-04).

        So does the person who raised it, which is what lets somebody revise their own proposal
        without holding a wider grant.
        """
        person_id = self._ctx.actor.person_id
        if person_id is not None and person_id in (
            proposal.routed_to_person_id,
            proposal.raised_by_person_id,
        ):
            return frozenset({Relation.PERSONAL})
        return frozenset()

    def _load(self, proposal_id: uuid.UUID) -> Proposal:
        proposal = repository.get_proposal(
            self._session, org_id=self._org_id, proposal_id=proposal_id
        )
        if proposal is None:
            raise EntityNotFound("proposal", proposal_id)
        return proposal

    def _authorize(
        self,
        action: AuthzAction,
        relations: frozenset[Relation],
        resource_id: uuid.UUID | None = None,
    ) -> AuthzDecision:
        return authorize(
            self._ctx.principal, action, _ref(self._org_id, relations, resource_id)
        )

    def _audit(
        self,
        action: AuthzAction,
        resource_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        decision: AuthzDecision,
    ) -> None:
        record_audit(
            self._session,
            org_id=self._org_id,
            actor=self._ctx.actor,
            action=action,
            resource_type=ResourceType.PROPOSAL,
            resource_id=resource_id,
            before=before,
            after=after,
            decision=decision,
        )

    def _emit(
        self, event_type: str, proposal: Proposal, extra: dict[str, Any] | None = None
    ) -> None:
        append_domain_event(
            self._session,
            org_id=self._org_id,
            type=event_type,
            aggregate_type="proposal",
            aggregate_id=proposal.id,
            payload={
                "proposal_id": str(proposal.id),
                "kind": proposal.kind,
                "target_type": proposal.target_type,
                "status": proposal.status,
                "action_hash": proposal.action_hash,
                **(extra or {}),
            },
            actor=self._ctx.actor,
        )

    def _emit_execution(
        self, record: ApprovalRecord, result: gateway.ExecutionResult
    ) -> None:
        append_domain_event(
            self._session,
            org_id=self._org_id,
            type="ProposalExecuted",
            aggregate_type="proposal",
            aggregate_id=record.proposal_id,
            payload={
                "proposal_id": str(record.proposal_id),
                "approval_record_id": str(record.id),
                "approved_action_hash": record.approved_action_hash,
                "resulting_entity_type": result.entity_type,
                "resulting_entity_id": str(result.entity_id),
            },
            actor=self._ctx.actor,
        )


#: The job kind that executes an approved Proposal (ADR-0044).
EXECUTE_APPROVAL = "execute_approval"


def enqueue_execution(
    session: Session, *, org_id: uuid.UUID, approval_id: uuid.UUID
) -> bool:
    """Queue an approved action for the worker. Idempotent on the approval.

    The dedupe key is the approval id, so a double-click or a retried request produces one job.
    Returns False when one is already queued, which is not an error — the caller asked for work
    that is already going to happen.
    """
    job = jobs.enqueue(
        session,
        org_id=org_id,
        kind=EXECUTE_APPROVAL,
        payload={"approval_id": str(approval_id)},
        dedupe_key=str(approval_id),
    )
    return job is not None


def execute_queued_approval(
    session: Session, *, org_id: uuid.UUID, payload: dict[str, Any]
) -> str:
    """The job handler. Runs inside the worker's transaction (ADR-0044).

    Everything that makes this safe already exists one layer down: `ProposalService.execute`
    recomputes the action hash and claims the ApprovalRecord with a conditional update. A
    redelivered job therefore finds the approval spent and returns without repeating the mutation,
    which is how at-least-once delivery becomes exactly-once execution.

    The principal is rebuilt from the approver on the record rather than carried in the payload: a
    queue row is data, and a payload that named its own authority would be a request field deciding
    permissions (ADR-0043).
    """
    approval_id = uuid.UUID(str(payload["approval_id"]))
    record = repository.get_approval(session, org_id=org_id, approval_id=approval_id)
    if record is None:
        raise EntityNotFound("approval_record", approval_id)

    if ExecutionStatus(record.execution_status) is not ExecutionStatus.PENDING:
        # Already done, or a rejection that authorises nothing. Either way the job is complete:
        # raising here would retry forever against a state that will never change.
        return f"already {record.execution_status}"

    # BR-AI-22, before anything else this handler does. Expiry is terminal rather than retryable:
    # an approval cannot become unexpired, so the job is dead rather than pending (ADR-0051).
    # Queued before the deadline and claimed after it, retried after it, or delivered twice with
    # the second arriving after it — all three land here.
    deadline = execution_deadline(record.decided_at)
    if dt.datetime.now(dt.UTC) >= deadline:
        raise TerminalJobError(
            f"BR-AI-22: the execution window closed at {deadline.isoformat()}; "
            "the action must be approved again"
        )

    principal = resolve_principal_for(session, org_id=org_id, person_id=record.approver_person_id)
    service = ProposalService(
        ServiceContext(
            session=session,
            principal=principal,
            actor=Actor(
                type=ActorType.PERSON,
                person_id=record.approver_person_id,
                extra={"executed_via": "job", "job_kind": EXECUTE_APPROVAL},
            ),
        )
    )
    outcome = service.execute(ExecuteApproval(approval_id=approval_id))
    return f"{outcome.entity_type}:{outcome.entity_id}"
