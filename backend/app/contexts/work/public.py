"""Published interface of the Work Core context.

Other contexts import from here and nowhere else inside this package (ADR-0001). Persistence models
are exported for the platform's schema tooling; the domain rules are exported because downstream
contexts (Commitment, Governance) need to reason about work status without reimplementing it.
"""

from app.contexts.work.domain import (
    AssignmentRole,
    AssignmentStatus,
    DependencyEdge,
    DependencyKind,
    DependencyStatus,
    DomainRuleViolation,
    Endpoint,
    MilestoneStatus,
    ProjectStatus,
    Source,
    WorkPartition,
    WorkStatus,
    current_owner,
    partition_of,
)
from app.contexts.work.models import (
    Dependency,
    Milestone,
    Project,
    Work,
    WorkAssignment,
)

__all__ = [
    "AssignmentRole",
    "AssignmentStatus",
    "Dependency",
    "DependencyEdge",
    "DependencyKind",
    "DependencyStatus",
    "DomainRuleViolation",
    "Endpoint",
    "Milestone",
    "MilestoneStatus",
    "Project",
    "ProjectStatus",
    "Source",
    "Work",
    "WorkAssignment",
    "WorkPartition",
    "WorkStatus",
    "current_owner",
    "partition_of",
]
