"""The matrix is the specification. These tests keep it from developing holes.

The valuable property here is not coverage of the code, it is that adding a Role, ResourceType or
Action fails the build until somebody decides, in writing, what it means for every existing cell.
Authorization bugs are silent, and a missing cell is exactly how one arrives.
"""

from __future__ import annotations

import pytest

from app.platform.authz.matrix import (
    MATRIX,
    RESOURCE_ACTIONS,
    declared_pairs,
    expected_pairs,
)
from app.platform.authz.model import Action, Grant, ResourceType, Role


def test_every_resource_action_pair_is_declared() -> None:
    missing = expected_pairs() - declared_pairs()
    assert not missing, (
        "undeclared authorization cells: "
        + ", ".join(sorted(f"{r.value}.{a.value}" for r, a in missing))
    )


def test_no_undeclared_pairs_in_the_matrix() -> None:
    extra = declared_pairs() - expected_pairs()
    assert not extra, (
        "matrix declares actions that are not applicable to the resource: "
        + ", ".join(sorted(f"{r.value}.{a.value}" for r, a in extra))
    )


def test_every_cell_names_every_role() -> None:
    for (resource, action), cell in MATRIX.items():
        assert set(cell) == set(Role), (
            f"{resource.value}.{action.value} does not declare a value for every role"
        )


def test_every_resource_type_has_at_least_one_action() -> None:
    for resource in ResourceType:
        assert RESOURCE_ACTIONS.get(resource), f"{resource.value} declares no actions"


def test_deny_is_never_combined_with_a_grant() -> None:
    """DENY means never. A cell holding both DENY and a grant is a contradiction, not a nuance."""
    for (resource, action), cell in MATRIX.items():
        for role, grants in cell.items():
            if Grant.DENY in grants:
                assert grants == frozenset({Grant.DENY}), (
                    f"{resource.value}.{action.value}/{role.value} mixes DENY with a grant"
                )


def test_viewer_and_auditor_never_receive_write_grants() -> None:
    """Read-only roles stay read-only. Easy to violate by copying a row."""
    write_actions = {
        Action.CREATE,
        Action.UPDATE,
        Action.CHANGE_STATE,
        Action.CHANGE_VISIBILITY,
        Action.ASSIGN,
        Action.REASSIGN,
        Action.END_ASSIGNMENT,
        Action.MANAGE_MEMBERS,
        Action.MANAGE_ROLES,
        Action.ATTACH,
    }
    for (resource, action), cell in MATRIX.items():
        if action not in write_actions:
            continue
        for role in (Role.VIEWER, Role.AUDITOR, Role.EXECUTIVE):
            assert cell[role] == frozenset({Grant.DENY}), (
                f"{role.value} has a write grant on {resource.value}.{action.value}"
            )


def test_only_admin_and_auditor_can_read_audit_entries() -> None:
    for action in (Action.READ, Action.LIST):
        cell = MATRIX[(ResourceType.AUDIT_ENTRY, action)]
        allowed = {role for role, grants in cell.items() if grants != frozenset({Grant.DENY})}
        assert allowed == {Role.ORG_ADMIN, Role.AUDITOR}


@pytest.mark.parametrize("resource", [ResourceType.WORK, ResourceType.PROJECT])
def test_member_can_read_work_and_projects_through_a_relationship(resource: ResourceType) -> None:
    cell = MATRIX[(resource, Action.READ)]
    assert cell[Role.MEMBER] != frozenset({Grant.DENY})


def test_member_can_create_work_anywhere_in_the_organization() -> None:
    """BR-W-07 and BR-W-15: work with no project and no team context must have somewhere to land."""
    cell = MATRIX[(ResourceType.WORK, Action.CREATE)]
    assert Grant.ORG in cell[Role.MEMBER]


def test_changing_who_owns_work_is_not_the_same_power_as_editing_it() -> None:
    """ADR-0032. A member may edit work they are related to; reassigning ownership is a lead's \
        call."""
    update = MATRIX[(ResourceType.WORK, Action.UPDATE)][Role.MEMBER]
    reassign = MATRIX[(ResourceType.WORK_ASSIGNMENT, Action.REASSIGN)][Role.MEMBER]
    assert update != frozenset({Grant.DENY})
    assert reassign == frozenset({Grant.DENY})
