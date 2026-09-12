"""The published interface of the Intelligence context (ADR-0001, ADR-0040).

Intelligence sits at the end of the dependency order and is the only context with several edges
outward, which is not an exception to ADR-0040 but the reason for it: executing an approved Proposal
*is* the act of reaching across contexts, so the reaching is concentrated here where it is reviewed.

Nothing imports this module today. It exists because the extraction checkpoint will, and because a
context whose interface is defined only when somebody first needs it tends to publish its internals.
"""

from app.contexts.intelligence.action import Action, hash_of
from app.contexts.intelligence.commands import (
    DecideProposal,
    ExecuteApproval,
    ProposedFieldChange,
    RaiseProposal,
    ReviseProposal,
)
from app.contexts.intelligence.domain import (
    EXECUTION_WINDOW,
    Decision,
    ExecutionStatus,
    ProposalKind,
    ProposalStatus,
    execution_deadline,
)
from app.contexts.intelligence.gateway import REGISTRY, REGISTRY_VERSION, Tool
from app.contexts.intelligence.intents import (
    IntentRefused,
    IntentValidator,
    ResolvedParticipant,
    ValidatedIntent,
    ValidationOutcome,
)
from app.contexts.intelligence.interactions import (
    FORBIDDEN_KEYS,
    StartInteraction,
    assert_no_secrets,
    calls_for,
    finish_interaction,
    get_interaction,
    list_interactions,
    record_tool_call,
    start_interaction,
)
from app.contexts.intelligence.models import (
    AIInteraction,
    ApprovalRecord,
    Proposal,
    ProposalEvidence,
    ProposedChange,
    ToolCall,
)
from app.contexts.intelligence.policy import (
    AgentCapabilityPolicy,
)
from app.contexts.intelligence.policy import (
    load as load_capability_policy,
)
from app.contexts.intelligence.policy import (
    rows_for as capability_policy_rows,
)
from app.contexts.intelligence.policy import (
    set_mode as set_capability_mode,
)
from app.contexts.intelligence.queries import (
    ProposalFilter,
    ProposalPage,
    approval_for,
    approval_for_id,
    changes_for_proposal,
    evidence_for_proposal,
    get_proposal,
    list_proposals,
    proposals_citing_evidence,
)
from app.contexts.intelligence.services import (
    EXECUTE_APPROVAL,
    ExecutionOutcome,
    ProposalService,
    ServiceContext,
    enqueue_execution,
    execute_queued_approval,
)

__all__ = [
    "ValidationOutcome",
    "ValidatedIntent",
    "ResolvedParticipant",
    "IntentValidator",
    "IntentRefused",
    "execution_deadline",
    "EXECUTION_WINDOW",
    "set_capability_mode",
    "load_capability_policy",
    "capability_policy_rows",
    "AgentCapabilityPolicy",
    "approval_for_id",
    "execute_queued_approval",
    "enqueue_execution",
    "EXECUTE_APPROVAL",
    "start_interaction",
    "record_tool_call",
    "list_interactions",
    "get_interaction",
    "finish_interaction",
    "calls_for",
    "assert_no_secrets",
    "ToolCall",
    "StartInteraction",
    "FORBIDDEN_KEYS",
    "AIInteraction",
    "REGISTRY",
    "REGISTRY_VERSION",
    "Action",
    "ApprovalRecord",
    "Decision",
    "DecideProposal",
    "ExecuteApproval",
    "ExecutionOutcome",
    "ExecutionStatus",
    "Proposal",
    "ProposalEvidence",
    "ProposalFilter",
    "ProposalKind",
    "ProposalPage",
    "ProposalService",
    "ProposalStatus",
    "ProposedChange",
    "ProposedFieldChange",
    "RaiseProposal",
    "ReviseProposal",
    "ServiceContext",
    "Tool",
    "approval_for",
    "changes_for_proposal",
    "evidence_for_proposal",
    "get_proposal",
    "hash_of",
    "list_proposals",
    "proposals_citing_evidence",
]
