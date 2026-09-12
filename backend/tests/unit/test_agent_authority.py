"""Agent identity and the authority intersection (BR-AI-03, ADR-0043).

The property being tested throughout is that the intersection can only ever *narrow*. No
combination of agent configuration and organization policy may produce a reach wider than the
person whose authority is being borrowed — which is what makes delegating to an agent a smaller
decision than it sounds.
"""

from __future__ import annotations

import uuid

import pytest

from app.platform.authz import Action, Principal, ResourceType, Role
from app.platform.authz.agent import (
    CAPABILITY_TOOLS,
    FORBIDDEN_ACTIONS,
    FORBIDDEN_RESOURCES,
    AgentAuthorityError,
    AgentCapability,
    AgentIdentity,
    AgentPrincipal,
    AutonomyMode,
    CapabilityPolicy,
    PolicyCell,
    assert_within_agent_authority,
)

ORG = uuid.uuid4()


def a_person(*roles: Role) -> Principal:
    return Principal(
        person_id=uuid.uuid4(), org_id=ORG, roles=frozenset(roles or (Role.MEMBER,))
    )


def an_agent(*capabilities: AgentCapability) -> AgentIdentity:
    return AgentIdentity(
        name="extractor",
        capabilities=frozenset(capabilities or (AgentCapability.EXTRACT,)),
    )


class TestIdentity:
    def test_an_agent_needs_a_name(self) -> None:
        with pytest.raises(ValueError, match="name"):
            AgentIdentity(name="  ", capabilities=frozenset({AgentCapability.EXTRACT}))

    def test_an_agent_with_no_capabilities_is_a_configuration_mistake(self) -> None:
        """Not a safe default worth allowing — an agent that can do nothing was meant to do
        something, and failing here is cheaper than a run that silently proposes nothing."""
        with pytest.raises(ValueError, match="capability"):
            AgentIdentity(name="idle", capabilities=frozenset())

    def test_capabilities_map_to_a_tool_vocabulary(self) -> None:
        assert an_agent(AgentCapability.EXTRACT).allowed_tools() == CAPABILITY_TOOLS[
            AgentCapability.EXTRACT
        ]

    def test_a_read_only_capability_reaches_no_tool(self) -> None:
        """`ANSWER` computes and displays (C-1c). Persisting anything would be a mutation."""
        assert an_agent(AgentCapability.ANSWER).allowed_tools() == frozenset()


def a_policy(*cells: tuple[str, str, str]) -> CapabilityPolicy:
    """A policy from `(capability, entity_type, action)` triples, all at level 1."""
    return CapabilityPolicy(
        cells=tuple(
            PolicyCell(
                capability=AgentCapability(capability),
                entity_type=entity_type,
                action=Action(action),
                mode=AutonomyMode.PROPOSE,
            )
            for capability, entity_type, action in cells
        )
    )


class TestDenyByDefault:
    def test_an_organization_that_has_decided_nothing_denies_everything(self) -> None:
        """ADR-0047, and the direction the two failure modes point in.

        A capability that is off when it should be on is a support ticket. One that is on when it
        should be off is an AI writing into somebody's organization without anybody having decided
        it should. So absence is denial, with no default row, no seed and no fallback.
        """
        policy = CapabilityPolicy()
        assert policy.mode_for(AgentCapability.EXTRACT, "work", Action.CREATE) is AutonomyMode.OFF
        assert not policy.allows(AgentCapability.EXTRACT, "work", Action.CREATE)
        assert policy.enabled_tools(frozenset({AgentCapability.EXTRACT})) == frozenset()

    def test_an_unrelated_cell_grants_nothing(self) -> None:
        policy = a_policy(("extract", "commitment", "create"))
        assert not policy.allows(AgentCapability.EXTRACT, "work", Action.CREATE)

    def test_an_explicit_off_denies(self) -> None:
        """A row saying `off` and no row at all are the same answer, deliberately.

        An explicit `off` is how an organization records that it *decided* rather than never
        looked — the audit entry is the difference, not the effect.
        """
        policy = CapabilityPolicy(
            cells=(
                PolicyCell(
                    capability=AgentCapability.EXTRACT,
                    entity_type="work",
                    action=Action.CREATE,
                    mode=AutonomyMode.OFF,
                ),
            )
        )
        assert not policy.allows(AgentCapability.EXTRACT, "work", Action.CREATE)
        assert policy.enabled_tools(frozenset({AgentCapability.EXTRACT})) == frozenset()

    def test_a_granted_cell_enables_its_tools(self) -> None:
        """The control. Without it a policy that denied everything would pass every test above."""
        policy = a_policy(("extract", "work", "create"))
        assert policy.allows(AgentCapability.EXTRACT, "work", Action.CREATE)
        assert "create_work" in policy.enabled_tools(frozenset({AgentCapability.EXTRACT}))


class TestIntersection:
    def test_the_effective_set_is_capability_intersect_policy(self) -> None:
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT),
            delegated=a_person(),
            policy=a_policy(("extract", "work", "create")),
        )
        assert principal.effective_tools() == frozenset({"create_work", "update_work_status"}) & (
            CAPABILITY_TOOLS[AgentCapability.EXTRACT]
        )
        assert principal.may_use("create_work")
        assert not principal.may_use("create_commitment")

    def test_a_policy_naming_a_capability_the_agent_lacks_grants_nothing(self) -> None:
        """The direction that matters most.

        Turning on `link_evidence` for an organization must not widen an agent that was never
        built to do it — otherwise a policy edit reaches further than the person making it could
        possibly know.
        """
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT),
            delegated=a_person(),
            policy=a_policy(("link_evidence", "work", "create")),
        )
        assert principal.effective_tools() == frozenset()

    def test_a_capability_the_policy_has_not_enabled_grants_nothing(self) -> None:
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT),
            delegated=a_person(),
            policy=CapabilityPolicy(),
        )
        assert principal.effective_tools() == frozenset()
        assert not principal.may_use("create_work")

    def test_neither_side_can_widen_the_other(self) -> None:
        """Both narrowings at once, which is what "intersection" has to mean here."""
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT),
            delegated=a_person(),
            policy=a_policy(
                ("extract", "work", "create"),
                ("extract", "commitment", "create"),
                ("link_evidence", "work", "create"),
            ),
        )
        tools = principal.effective_tools()
        assert "create_work" in tools
        assert "create_commitment" in tools
        # Nothing from the capability the agent does not hold, and nothing outside the registry.
        assert tools <= CAPABILITY_TOOLS[AgentCapability.EXTRACT]

    def test_an_agent_holds_exactly_its_delegate_s_roles(self) -> None:
        """There is no mechanism for adding one, which is the point.

        An agent working for a member is a member. It cannot become an admin by being configured
        differently, because its roles are read from the person, not from its own record.
        """
        person = a_person(Role.MEMBER)
        principal = AgentPrincipal(identity=an_agent(), delegated=person)
        assert principal.roles == person.roles
        assert principal.org_id == person.org_id
        assert principal.person_id == person.person_id

    def test_policy_cannot_unlock_a_forbidden_action(self) -> None:
        """The refusals below are not policy-configurable at all.

        A row cannot switch on membership, roles or approval — they are applied after the
        intersection and are not keyed on anything an organization can edit.
        """
        AgentPrincipal(
            identity=an_agent(),
            delegated=a_person(Role.ORG_ADMIN),
            policy=a_policy(("extract", "work", "create")),
        )
        with pytest.raises(AgentAuthorityError):
            assert_within_agent_authority(Action.MANAGE_ROLES, ResourceType.ROLE_ASSIGNMENT)

    def test_level_three_is_not_representable(self) -> None:
        """BR-AI-31. Not disabled, not guarded — absent from the type."""
        assert {mode.value for mode in AutonomyMode} == {
            "off",
            "level_1_propose",
            "level_2_approved_execution",
        }


class TestAbsoluteRefusals:
    @pytest.mark.parametrize("action", sorted(FORBIDDEN_ACTIONS))
    def test_forbidden_actions_are_refused(self, action: Action) -> None:
        with pytest.raises(AgentAuthorityError):
            assert_within_agent_authority(action, ResourceType.WORK)

    @pytest.mark.parametrize("resource", sorted(FORBIDDEN_RESOURCES))
    def test_forbidden_resources_are_refused(self, resource: ResourceType) -> None:
        """BR-AI-08, BR-AI-23. Identity and governance are outside the vocabulary entirely."""
        with pytest.raises(AgentAuthorityError):
            assert_within_agent_authority(Action.CREATE, resource)

    def test_approving_is_never_an_agent_action(self) -> None:
        """The one that would collapse the whole design: an agent that could approve its own
        Proposal would be Level 3 wearing Level 2's schema."""
        assert Action.APPROVE in FORBIDDEN_ACTIONS

    def test_an_ordinary_work_creation_is_allowed(self) -> None:
        """The control. Without it, a blanket refusal would pass every test above."""
        assert_within_agent_authority(Action.CREATE, ResourceType.WORK)

    def test_no_capability_reaches_a_forbidden_tool(self) -> None:
        """Two independent gates: no such tool exists (ADR-0042), and no capability names one."""
        every_tool = {tool for tools in CAPABILITY_TOOLS.values() for tool in tools}
        for word in ("delete", "remove", "grant", "revoke", "member", "role", "send", "approve"):
            assert not any(word in tool for tool in every_tool), (
                f"a capability reaches a tool containing '{word}'"
            )
