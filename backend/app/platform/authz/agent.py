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


class AutonomyMode(enum.StrEnum):
    """BR-AI-30, BR-AI-31. The MVP ceiling is level 2, and nothing above it is representable.

    Not "disabled", not guarded by a check somewhere — absent from the type, so a Level 3 policy
    cannot be written down, stored, or arrived at by editing a row.
    """

    OFF = "off"
    PROPOSE = "level_1_propose"
    APPROVED_EXECUTION = "level_2_approved_execution"


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyCell:
    """One row of `agent_capability_policy` (ADR-0047)."""

    capability: AgentCapability
    entity_type: str
    action: Action
    mode: AutonomyMode


@dataclasses.dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """An organization's autonomy policy, as a deterministic function over its rows.

    **Absence is denial.** There is no default row, no fallback and no wildcard: a cell with no
    policy is `off`. That is the opposite of how permission tables usually drift, and it is chosen
    because the two failure modes are not symmetric — a capability that is off when it should be on
    is a support ticket, and one that is on when it should be off is an AI writing into somebody's
    organization without anybody having decided it should.
    """

    cells: tuple[PolicyCell, ...] = ()

    def mode_for(
        self, capability: AgentCapability, entity_type: str, action: Action
    ) -> AutonomyMode:
        for cell in self.cells:
            if (
                cell.capability is capability
                and cell.entity_type == entity_type
                and cell.action is action
            ):
                return cell.mode
        return AutonomyMode.OFF

    def allows(
        self, capability: AgentCapability, entity_type: str, action: Action
    ) -> bool:
        return self.mode_for(capability, entity_type, action) is not AutonomyMode.OFF

    def enabled_tools(self, capabilities: frozenset[AgentCapability]) -> frozenset[str]:
        """Which tools the policy turns on, for capabilities the agent actually holds.

        Three narrowings, all intersections, none of them trusting the row on its own:

        * the **capability** must be one this agent holds — a row naming a capability it was never
          built with grants nothing, so a policy edit cannot widen an agent past its own design;
        * the **entity type and action together** must name tools that exist, which is what
          `TOOLS_FOR_CELL` answers;
        * the mode must not be `off`.

        The action half of the second narrowing is CP19A's correction. This used to consult
        `(capability, entity_type)` and ignore `cell.action` entirely, so a cell decided about
        *creating* work also turned on every other tool touching work. Nothing widened in practice —
        `EXTRACT` happens to hold no state-changing tool — but the policy's most specific field was
        not consulted at the point that decides, so the row read as more precise than it was.
        """
        allowed: set[str] = set()
        for cell in self.cells:
            if cell.mode is AutonomyMode.OFF or cell.capability not in capabilities:
                continue
            allowed |= CAPABILITY_TOOLS[cell.capability] & TOOLS_FOR_CELL.get(
                (cell.entity_type, cell.action), frozenset()
            )
        return frozenset(allowed)


#: Which tools perform which `(entity type, action)`. The policy is keyed that way (BR-AI-30) and
#: the registry on tool name, so something has to relate them; keeping it here rather than importing
#: the registry keeps `platform` free of a dependency on a context.
#:
#: This is also the *whole* surface a policy can be written about. A cell outside it is not a
#: narrower permission — it is a row that can never grant anything, and CP19A refuses to store one
#: rather than letting an administrator decide something the system cannot act on.
TOOLS_FOR_CELL: dict[tuple[str, Action], frozenset[str]] = {
    ("work", Action.CREATE): frozenset({"create_work"}),
    ("work", Action.CHANGE_STATE): frozenset({"update_work_status"}),
    ("work_assignment", Action.ASSIGN): frozenset({"assign_work"}),
    ("commitment", Action.CREATE): frozenset({"create_commitment"}),
    ("commitment", Action.CHANGE_STATE): frozenset({"change_commitment_status"}),
}


def tools_for_cell(
    capability: AgentCapability, entity_type: str, action: Action
) -> frozenset[str]:
    """The tools one policy cell could turn on. Empty means the cell decides nothing."""
    return CAPABILITY_TOOLS[capability] & TOOLS_FOR_CELL.get((entity_type, action), frozenset())


def policy_surface() -> tuple[PolicyCell, ...]:
    """Every cell that could ever grant a tool, with no mode attached.

    Derived from the two tables above rather than written down a third time: a capability whose
    tools never intersect a cell's tools cannot appear, so `link_evidence` — which holds no tools
    yet — produces no rows and an administrator is not offered a switch that does nothing.

    Ordered so the UI, the API and the tests all see the same grid in the same order.
    """
    cells = [
        PolicyCell(
            capability=capability, entity_type=entity_type, action=action, mode=AutonomyMode.OFF
        )
        for capability in AgentCapability
        for entity_type, action in TOOLS_FOR_CELL
        if tools_for_cell(capability, entity_type, action)
    ]
    return tuple(
        sorted(
            cells,
            key=lambda cell: (cell.capability.value, cell.entity_type, cell.action.value),
        )
    )


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
    #: The organization's policy, read from `agent_capability_policy` (ADR-0047). An empty policy
    #: denies everything, which is what a newly created organization has.
    policy: CapabilityPolicy = dataclasses.field(default_factory=CapabilityPolicy)

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

        Capability ∩ policy, and the intersection is taken in both directions: a capability the
        policy has not enabled grants nothing, and a policy naming a capability the agent does not
        hold grants nothing either. Neither side can widen the other, which is the property that
        makes editing a policy row a bounded decision.

        The remaining narrowings are applied where they belong: the delegate's authority by the
        application service that authorizes `delegated`, the organization scope by RLS on the
        session, and the absolute refusals by `assert_within_agent_authority`.
        """
        return self.identity.allowed_tools() & self.policy.enabled_tools(
            self.identity.capabilities
        )

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
