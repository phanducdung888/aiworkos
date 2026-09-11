"""Authorization decisions.

Three layers of confidence, in increasing order of usefulness:

1. An exhaustive sweep over every role × resource × action × relationship combination, asserting the
   engine agrees with the matrix. This catches engine bugs, not matrix bugs.
2. Hand-written expectations that would catch a wrong matrix cell, because a sweep derived from the
   matrix cannot.
3. The tenant check, which must beat everything else regardless of role.
"""

from __future__ import annotations

import itertools
import uuid

import pytest

from app.platform.authz import (
    Action,
    AuthorizationError,
    Principal,
    Relation,
    ResourceRef,
    ResourceType,
    Role,
    authorize,
    can,
)
from app.platform.authz.matrix import MATRIX, RESOURCE_ACTIONS
from app.platform.authz.model import GRANT_REQUIRES, Grant

ORG_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")
PERSON = uuid.UUID("00000000-0000-4000-8000-000000000001")

RELATION_SETS = [
    frozenset(),
    frozenset({Relation.IN_DEPARTMENT}),
    frozenset({Relation.IN_TEAM}),
    frozenset({Relation.PERSONAL}),
    frozenset({Relation.SELF}),
    frozenset({Relation.IN_TEAM, Relation.PERSONAL}),
]


def principal(*roles: Role, org: uuid.UUID = ORG_A) -> Principal:
    return Principal(person_id=PERSON, org_id=org, roles=frozenset(roles))


def resource(
    kind: ResourceType, *, org: uuid.UUID = ORG_A, relations: frozenset[Relation] = frozenset()
) -> ResourceRef:
    return ResourceRef(type=kind, org_id=org, id=uuid.uuid4(), relations=relations)


# --------------------------------------------------------------------------- exhaustive sweep


def _expected(role: Role, kind: ResourceType, action: Action,
    relations: frozenset[Relation]) -> bool:
    grants = MATRIX[(kind, action)][role]
    available = set(relations) | {Relation.SAME_ORG}
    for grant in grants:
        if grant is Grant.DENY:
            continue
        required = GRANT_REQUIRES[grant]
        if required is None or required in available:
            return True
    return False


def test_every_combination_matches_the_matrix() -> None:
    """Every role × resource × action × relationship. Roughly 2.5k cells, and it runs in a blink."""
    checked = 0
    for kind, actions in RESOURCE_ACTIONS.items():
        for action, role, relations in itertools.product(actions, Role, RELATION_SETS):
            decision = can(principal(role), action, resource(kind, relations=relations))
            assert decision.allowed is _expected(role, kind, action, relations), (
                f"{role.value} {action.value} {kind.value} with {sorted(r.value for r in \
                    relations)}"
            )
            checked += 1
    assert checked > 1000, "the sweep is not covering what it claims to cover"


def test_a_decision_always_explains_itself() -> None:
    for kind, actions in RESOURCE_ACTIONS.items():
        for action, role in itertools.product(actions, Role):
            decision = can(principal(role), action,
                resource(kind, relations=frozenset({Relation.PERSONAL})))
            assert decision.reason, f"{role.value}/{kind.value}/{action.value} gave no reason"
            if decision.allowed:
                assert decision.matched_role is role
                assert decision.matched_grant is not None


# --------------------------------------------------------------------------- tenancy


def test_cross_organization_access_is_refused_for_every_role() -> None:
    """BR-G-01. No role, relation or matrix cell may grant across an organization boundary."""
    for role in Role:
        for kind, actions in RESOURCE_ACTIONS.items():
            for action in actions:
                decision = can(
                    principal(role),
                    action,
                    resource(kind, org=ORG_B, relations=frozenset(RELATION_SETS[-1])),
                )
                assert not decision.allowed
                assert "cross-organization" in decision.reason


def test_org_admin_of_one_org_is_nobody_in_another() -> None:
    decision = can(principal(Role.ORG_ADMIN), Action.UPDATE,
        resource(ResourceType.ORGANIZATION, org=ORG_B))
    assert not decision.allowed


# --------------------------------------------------------------------------- hand-written cases


def test_member_may_create_work_with_no_project_and_no_team() -> None:
    """The capture case: "someone please check the IOC API before Friday" (BR-W-07, BR-W-15)."""
    assert can(principal(Role.MEMBER), Action.CREATE, resource(ResourceType.WORK)).allowed


def test_member_may_not_reassign_ownership_of_work_they_own() -> None:
    """ADR-0032: editing work and changing who owns it are different powers."""
    own = frozenset({Relation.PERSONAL})
    assert can(principal(Role.MEMBER), Action.UPDATE,
        resource(ResourceType.WORK, relations=own)).allowed
    assert not can(
        principal(Role.MEMBER), Action.REASSIGN, resource(ResourceType.WORK_ASSIGNMENT,
            relations=own)
    ).allowed


def test_team_lead_may_reassign_within_their_team_but_not_outside_it() -> None:
    in_team = frozenset({Relation.IN_TEAM})
    assert can(
        principal(Role.TEAM_LEAD), Action.REASSIGN, resource(ResourceType.WORK_ASSIGNMENT,
            relations=in_team)
    ).allowed
    assert not can(
        principal(Role.TEAM_LEAD), Action.REASSIGN, resource(ResourceType.WORK_ASSIGNMENT)
    ).allowed


def test_executive_reads_broadly_and_writes_nothing() -> None:
    assert can(principal(Role.EXECUTIVE), Action.LIST, resource(ResourceType.PROJECT)).allowed
    assert not can(principal(Role.EXECUTIVE), Action.UPDATE, resource(ResourceType.PROJECT)).allowed


def test_auditor_reads_the_audit_log_and_cannot_write_anything() -> None:
    assert can(principal(Role.AUDITOR), Action.LIST, resource(ResourceType.AUDIT_ENTRY)).allowed
    assert not can(principal(Role.AUDITOR), Action.CREATE, resource(ResourceType.WORK)).allowed


def test_member_cannot_read_the_audit_log() -> None:
    assert not can(principal(Role.MEMBER), Action.READ, resource(ResourceType.AUDIT_ENTRY)).allowed


def test_a_person_may_update_their_own_record_but_not_a_colleague_s() -> None:
    assert can(
        principal(Role.MEMBER), Action.UPDATE, resource(ResourceType.PERSON,
            relations=frozenset({Relation.SELF}))
    ).allowed
    assert not can(principal(Role.MEMBER), Action.UPDATE, resource(ResourceType.PERSON)).allowed


def test_only_org_admin_manages_roles() -> None:
    for role in Role:
        decision = can(principal(role), Action.MANAGE_ROLES, resource(ResourceType.ORGANIZATION))
        assert decision.allowed is (role is Role.ORG_ADMIN)


def test_holding_several_roles_takes_the_most_permissive() -> None:
    both = principal(Role.VIEWER, Role.TEAM_LEAD)
    assert can(both, Action.UPDATE,
        resource(ResourceType.TEAM, relations=frozenset({Relation.IN_TEAM}))).allowed


def test_an_action_that_does_not_apply_to_a_resource_is_refused_not_ignored() -> None:
    decision = can(principal(Role.ORG_ADMIN), Action.ASSIGN, resource(ResourceType.DEPARTMENT))
    assert not decision.allowed
    assert "is not an action on" in decision.reason


# --------------------------------------------------------------------------- the raising form


def test_authorize_raises_and_carries_the_decision() -> None:
    with pytest.raises(AuthorizationError) as excinfo:
        authorize(principal(Role.MEMBER), Action.MANAGE_ROLES, resource(ResourceType.ORGANIZATION))
    assert excinfo.value.decision.allowed is False
    assert excinfo.value.action is Action.MANAGE_ROLES


def test_authorize_returns_the_decision_when_allowed() -> None:
    decision = authorize(principal(Role.ORG_ADMIN), Action.READ,
        resource(ResourceType.ORGANIZATION))
    assert decision.allowed
    assert decision.matched_role is Role.ORG_ADMIN


def test_a_principal_must_hold_at_least_one_role() -> None:
    with pytest.raises(ValueError):
        Principal(person_id=PERSON, org_id=ORG_A, roles=frozenset())
