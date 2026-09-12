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


class TestIntersection:
    def test_the_effective_set_is_capability_intersect_policy(self) -> None:
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT),
            delegated=a_person(),
            policy_allows=frozenset({"create_work"}),
        )
        assert principal.effective_tools() == frozenset({"create_work"})

    def test_policy_cannot_widen_beyond_capability(self) -> None:
        """The direction that matters. A policy naming a tool the agent cannot do is not a grant."""
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT),
            delegated=a_person(),
            policy_allows=frozenset({"create_work", "delete_everything", "grant_role"}),
        )
        assert principal.effective_tools() == frozenset(
            {"create_work"}
        ) | (CAPABILITY_TOOLS[AgentCapability.EXTRACT] & principal.policy_allows)
        assert "delete_everything" not in principal.effective_tools()
        assert "grant_role" not in principal.effective_tools()

    def test_capability_cannot_widen_beyond_policy(self) -> None:
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT),
            delegated=a_person(),
            policy_allows=frozenset(),
        )
        assert principal.effective_tools() == frozenset()

    def test_an_empty_policy_denies_everything(self) -> None:
        principal = AgentPrincipal(
            identity=an_agent(AgentCapability.EXTRACT), delegated=a_person()
        )
        assert not principal.may_use("create_work")

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

    def test_borrowing_an_admin_s_authority_does_not_unlock_forbidden_actions(self) -> None:
        """The refusals below are not questions of degree.

        A delegating `org_admin` could change a role; an agent borrowing their authority still
        cannot, and the reason is not that the admin lacks the permission.
        """
        AgentPrincipal(identity=an_agent(), delegated=a_person(Role.ORG_ADMIN))
        with pytest.raises(AgentAuthorityError):
            assert_within_agent_authority(
                Action.MANAGE_ROLES, ResourceType.ROLE_ASSIGNMENT
            )


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
