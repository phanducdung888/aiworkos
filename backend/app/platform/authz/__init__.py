"""Centralized authorization. Nothing outside this package decides who may do what."""

from app.platform.authz.model import (
    Action,
    AuthorizationError,
    Decision,
    Grant,
    Principal,
    Relation,
    ResourceRef,
    ResourceType,
    Role,
)
from app.platform.authz.policy import authorize, can, grants_for, roles_for

__all__ = [
    "Action",
    "AuthorizationError",
    "Decision",
    "Grant",
    "Principal",
    "Relation",
    "ResourceRef",
    "ResourceType",
    "Role",
    "authorize",
    "can",
    "grants_for",
    "roles_for",
]
