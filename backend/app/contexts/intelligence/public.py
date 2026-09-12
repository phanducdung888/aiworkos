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
    Decision,
    ExecutionStatus,
    ProposalKind,
    ProposalStatus,
)
from app.contexts.intelligence.gateway import REGISTRY, Tool
from app.contexts.intelligence.models import (
    ApprovalRecord,
    Proposal,
    ProposalEvidence,
    ProposedChange,
)
from app.contexts.intelligence.queries import (
    ProposalFilter,
    ProposalPage,
    approval_for,
    changes_for_proposal,
    evidence_for_proposal,
    get_proposal,
    list_proposals,
    proposals_citing_evidence,
)
from app.contexts.intelligence.services import (
    ExecutionOutcome,
    ProposalService,
    ServiceContext,
)

__all__ = [
    "REGISTRY",
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
