"""The single place an authorization decision is made.

Application services call `authorize`. Routers never call it directly and never re-implement it.
Every decision is explainable: the returned `Decision` names the role and grant that allowed it, or
the reason it was refused, which is what makes the matrix testable and the audit trail meaningful.
"""

from __future__ import annotations

from app.platform.authz.matrix import MATRIX, RESOURCE_ACTIONS
from app.platform.authz.model import (
    GRANT_REQUIRES,
    Action,
    AuthorizationError,
    Decision,
    Grant,
    Principal,
    Relation,
    ResourceRef,
    Role,
)


def can(principal: Principal, action: Action, resource: ResourceRef) -> Decision:
    """Decide whether `principal` may perform `action` on `resource`.

    Order matters. The tenant check runs first and is unconditional: no role, relation or matrix
    cell can grant access across an organization boundary (BR-G-01).
    """
    if principal.org_id != resource.org_id:
        return Decision(False, "cross-organization access is never permitted")

    applicable = RESOURCE_ACTIONS.get(resource.type)
    if applicable is None or action not in applicable:
        return Decision(False, f"{action.value} is not an action on {resource.type.value}")

    cell = MATRIX.get((resource.type, action))
    if cell is None:  # pragma: no cover - the coverage test makes this unreachable
        return Decision(False, "no matrix entry declared")

    relations = set(resource.relations) | {Relation.SAME_ORG}

    best_denial = "no role held by this principal grants this action"
    for role in principal.roles:
        grants = cell.get(role, frozenset())
        for grant in _ordered(grants):
            if grant is Grant.DENY:
                continue
            required = GRANT_REQUIRES[grant]
            if required is None or required in relations:
                return Decision(
                    True,
                    f"{role.value} granted via {grant.value}",
                    matched_role=role,
                    matched_grant=grant,
                )
            best_denial = (
                f"{role.value} would be granted via {grant.value} "
                f"but the actor is not {required.value} for this resource"
            )
    return Decision(False, best_denial)


def authorize(principal: Principal, action: Action, resource: ResourceRef) -> Decision:
    """`can`, but raising. Use this at the application-service boundary."""
    decision = can(principal, action, resource)
    if not decision.allowed:
        raise AuthorizationError(decision, action, resource)
    return decision


def _ordered(grants: frozenset[Grant]) -> list[Grant]:
    """Evaluate broader grants first so the decision reason names the strongest applicable one."""
    order = {
        Grant.ORG: 0,
        Grant.DEPARTMENT: 1,
        Grant.TEAM: 2,
        Grant.PERSONAL: 3,
        Grant.SELF: 4,
        Grant.DENY: 5,
    }
    return sorted(grants, key=lambda g: order[g])


def roles_for(*roles: Role) -> frozenset[Role]:
    """Small helper so callers and tests build role sets the same way."""
    return frozenset(roles)
