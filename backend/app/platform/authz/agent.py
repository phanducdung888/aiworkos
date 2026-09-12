"""Agent identity and the authority intersection (BR-AI-03, ADR-0043).

An agent never has authority of its own. It has a *capability set* — what this kind of agent is
built to do — and it borrows a person's authority to do any of it. What it can actually reach is the
intersection:

    agent capability  ∩  delegated human authority  ∩  capability policy  ∩  organization scope

All four, every time. The shape matters: an intersection can only ever narrow, so no combination of
agent configuration and policy can produce a reach wider than the human whose authority is being
borrowed. An agent working on behalf of a `member` cannot do what an `org_admin` could, regardless
of how it is registered.

The delegated principal is what the application authorizes. That is deliberate and is what makes
BR-PR-05 true downstream: when an approved Proposal executes, the service authorizes the approving
*person*, not the agent — so approving something never grants anyone a power they lack.
"""

from __future__ import annotations

import dataclasses
import enum
import uuid

from app.platform.authz.model import Action, Principal, ResourceType, Role


class AgentCapability(enum.StrEnum):
    """What a kind of agent is built to do.

    Narrow on purpose. A capability is not a permission — it is the set of operations this agent
    *could* attempt, before any question of whose authority it is using. Anything absent here cannot
    be attempted at all, which is the first of the four narrowings.
    """

    #: Read an Event and propose Work, Commitments or citations from it (BR-AI-16).
    EXTRACT = "extract"
    #: Attach Evidence to an entity that already exists. Level 1 by C-1a.
    LINK_EVIDENCE = "link_evidence"
    #: Explain a deterministically detected condition. Level 1 by C-1b.
    EXPLAIN = "explain"
    #: Read-side answers that are never persisted. No Proposal required by C-1c.
    ANSWER = "answer"


#: Which tools each capability may reach. The Tool Gateway's registry is the vocabulary; this says
#: which words of it a given capability is allowed to use.
#:
#: Nothing here reaches a tool that deletes, changes membership or sends a message, because no such
#: tool exists (ADR-0042). If one were ever added, it would still be unreachable until a capability
#: named it — two independent gates rather than one.
CAPABILITY_TOOLS: dict[AgentCapability, frozenset[str]] = {
    AgentCapability.EXTRACT: frozenset(
        {"create_work", "create_commitment", "assign_work"}
    ),
    AgentCapability.LINK_EVIDENCE: frozenset(),
    AgentCapability.EXPLAIN: frozenset(),
    AgentCapability.ANSWER: frozenset(),
}

#: Actions an agent may never take, whatever its capabilities and whoever delegated to it.
#:
#: Listed as a positive refusal rather than left implicit in the absence of a tool, because the two
#: protect against different mistakes: the absent tool stops today's code, and this stops a future
#: registry entry from becoming reachable by an agent because nobody remembered.
FORBIDDEN_ACTIONS: frozenset[Action] = frozenset(
    {Action.MANAGE_MEMBERS, Action.MANAGE_ROLES, Action.APPROVE}
)

#: Resources an agent may never act on, at any autonomy level (BR-AI-08, BR-AI-23).
FORBIDDEN_RESOURCES: frozenset[ResourceType] = frozenset(
    {
        ResourceType.ORGANIZATION,
        ResourceType.DEPARTMENT,
        ResourceType.TEAM,
        ResourceType.PERSON,
        ResourceType.ORGANIZATION_MEMBERSHIP,
        ResourceType.ROLE_ASSIGNMENT,
        ResourceType.EXTERNAL_IDENTITY,
        ResourceType.AUDIT_ENTRY,
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class AgentIdentity:
    """A registered agent. Authenticated, not asserted.

    `name` is a stable service-account identifier that appears in `ai_interaction.agent_identity`
    and in every audit entry the run produces. There is no secret here: authentication happens
    before an identity is constructed, and the credential never enters the domain.
    """

    name: str
    capabilities: frozenset[AgentCapability]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("an agent identity requires a name")
        if not self.capabilities:
            # An agent with no capabilities can do nothing, which is a configuration mistake worth
            # failing on rather than a safe default worth allowing.
            raise ValueError("an agent identity requires at least one capability")

    def allowed_tools(self) -> frozenset[str]:
        tools: set[str] = set()
        for capability in self.capabilities:
            tools |= CAPABILITY_TOOLS[capability]
        return frozenset(tools)


@dataclasses.dataclass(frozen=True, slots=True)
class AgentPrincipal:
    """An authenticated agent acting under a named person's authority, in one organization.

    The object every agent operation is carried out with. It exists only if all three parts exist:
    there is no anonymous agent, no agent without a delegating human in this MVP, and no agent whose
    organization was inferred from a request field.
    """

    identity: AgentIdentity
    #: The human whose authority is borrowed. This is what the application authorizes.
    delegated: Principal
    #: Autonomy policy for this organization and capability, keyed as BR-AI-30 requires.
    policy_allows: frozenset[str] = frozenset()

    @property
    def org_id(self) -> uuid.UUID:
        return self.delegated.org_id

    @property
    def person_id(self) -> uuid.UUID:
        return self.delegated.person_id

    @property
    def roles(self) -> frozenset[Role]:
        """The delegating person's roles, unchanged.

        An agent's reach is its delegate's reach, narrowed. There is no role an agent holds that a
        person does not, because there is no mechanism here for adding one.
        """
        return self.delegated.roles

    def effective_tools(self) -> frozenset[str]:
        """BR-AI-03's intersection, for the tool vocabulary.

        Capability ∩ policy. The remaining two narrowings — the delegate's authority and the
        organization scope — are applied where they belong: by the application service that
        authorizes `delegated`, and by RLS on the session.
        """
        return self.identity.allowed_tools() & self.policy_allows

    def may_use(self, tool_name: str) -> bool:
        return tool_name in self.effective_tools()


def assert_within_agent_authority(
    action: Action, resource_type: ResourceType
) -> None:
    """The refusals that hold regardless of delegation (BR-AI-08, BR-AI-23, BR-AI-25).

    Checked before any grant is consulted, because these are not questions of degree. A delegating
    `org_admin` could change a role; an agent borrowing their authority still cannot, and the reason
    is not that the admin lacks the permission.
    """
    if action in FORBIDDEN_ACTIONS:
        raise AgentAuthorityError(
            f"an agent may not perform {action.value}, whatever authority it borrows"
        )
    if resource_type in FORBIDDEN_RESOURCES:
        raise AgentAuthorityError(
            f"an agent may not act on {resource_type.value} (BR-AI-08, BR-AI-23)"
        )


class AgentAuthorityError(Exception):
    """An agent attempted something outside its authority. Always audited as a denial."""
