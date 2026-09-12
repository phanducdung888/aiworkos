"""Identity & Organization domain rules.

Pure. No I/O, no ORM, no session — values in, values or exceptions out, the same shape as the Work
Core's domain module and for the same reason: rules that can be tested exhaustively in microseconds
get tested exhaustively.

Migration 0002 already enforces several of these in the database — the department tree trigger, the
partial unique indexes on open memberships and role grants, the check constraints on status. That
duplication is deliberate. The database is the backstop that survives a repair script; this layer
is where the *reason* lives and where a caller gets an error naming the rule they broke.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.platform.errors import DomainRuleViolation

#: BR-I-01. Not a preference: a deeper tree makes the recursive reach queries in `queries.py`
#: proportionally more expensive on a path that runs on every authorization decision.
MAX_DEPARTMENT_DEPTH = 5

#: BR-I-06. Below this, a mapping may suggest and must not attribute. Stored 0–100 because the
#: column is a smallint; the rule is written as 0.90 and means the same thing.
MIN_ATTRIBUTION_CONFIDENCE = 90


class PersonStatus(enum.StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPARTED = "departed"


class MembershipStatus(enum.StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ENDED = "ended"


class UnitStatus(enum.StrEnum):
    """Departments and teams share a lifecycle: they are archived, never deleted (BR-G-04)."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class TeamRole(enum.StrEnum):
    LEAD = "lead"
    MEMBER = "member"
    GUEST = "guest"


class ScopeType(enum.StrEnum):
    ORGANIZATION = "organization"
    DEPARTMENT = "department"
    TEAM = "team"
    PROJECT = "project"


#: BR-I-05, and the reason `departed` is not just a label: it is the state that stops new work
#: arriving. `inactive` is a person on leave — they keep what they hold and take nothing new either,
#: but the distinction is theirs to make, not the system's.
ASSIGNABLE_STATUSES = frozenset({PersonStatus.ACTIVE})

PERSON_TRANSITIONS: dict[PersonStatus, frozenset[PersonStatus]] = {
    PersonStatus.ACTIVE: frozenset({PersonStatus.INACTIVE, PersonStatus.DEPARTED}),
    PersonStatus.INACTIVE: frozenset({PersonStatus.ACTIVE, PersonStatus.DEPARTED}),
    # Departure is not reversible by a status change. Somebody returning is a decision to make
    # again, with an audit entry of its own, not a flag flipped back.
    PersonStatus.DEPARTED: frozenset(),
}


# --------------------------------------------------------------------------- organization


def validate_organization_update(*, name: str, timezone: str) -> None:
    if not name.strip():
        raise DomainRuleViolation("BR-G-01", "an organization requires a name")
    if not timezone.strip():
        raise DomainRuleViolation(
            "BR-G-05",
            "an organization requires a timezone; dates that people commit to are interpreted "
            "in it",
        )


# --------------------------------------------------------------------------- person


def validate_person_creation(*, display_name: str, email: str | None) -> PersonStatus:
    """BR-I-04, ADR-0036. Returns the status a new Person starts in.

    No Keycloak subject is required. A Person who cannot sign in can still own work and be named as
    a committer, which is what makes it possible to record that somebody outside the system promised
    something (BR-I-04).
    """
    if not display_name.strip():
        raise DomainRuleViolation("BR-I-04", "a person requires a display name")
    if email is not None and email.strip() and "@" not in email:
        raise DomainRuleViolation("BR-I-04", "an email address must contain '@'")
    return PersonStatus.ACTIVE


def validate_person_transition(current: PersonStatus, target: PersonStatus) -> None:
    if current == target:
        raise DomainRuleViolation("BR-I-05", f"this person is already {target}")
    if target not in PERSON_TRANSITIONS[current]:
        permitted = ", ".join(sorted(PERSON_TRANSITIONS[current])) or "nothing"
        raise DomainRuleViolation(
            "BR-I-05", f"{current} may move to {permitted}, not {target}"
        )


def assert_assignable(person_status: PersonStatus | str) -> None:
    """BR-I-05, stated once so every caller refuses the same way."""
    if PersonStatus(person_status) not in ASSIGNABLE_STATUSES:
        raise DomainRuleViolation(
            "BR-I-05", f"a person with status {person_status} cannot receive new work"
        )


# --------------------------------------------------------------------------- department


def validate_department_placement(
    *,
    department_id: uuid.UUID | None,
    parent_id: uuid.UUID | None,
    ancestors: Sequence[uuid.UUID],
) -> None:
    """BR-I-01. `ancestors` is the parent chain above `parent_id`, nearest first.

    Three failures, and the third is the one people forget. A department cannot be its own parent;
    the chain cannot come back round to it; and the tree has a depth limit, which is checked by
    counting rather than by trusting that nobody built a long one.
    """
    if parent_id is None:
        return
    if department_id is not None and parent_id == department_id:
        raise DomainRuleViolation("BR-I-01", "a department cannot be its own parent")
    chain = list(ancestors)
    if department_id is not None and department_id in chain:
        raise DomainRuleViolation("BR-I-01", "the department hierarchy would contain a cycle")
    depth = len(chain) + 2  # the ancestors, the parent itself, and this department
    if depth > MAX_DEPARTMENT_DEPTH:
        raise DomainRuleViolation(
            "BR-I-01",
            f"the department hierarchy would be {depth} deep; the maximum is "
            f"{MAX_DEPARTMENT_DEPTH}",
        )


def validate_unit_name(rule: str, name: str, what: str) -> None:
    if not name.strip():
        raise DomainRuleViolation(rule, f"a {what} requires a name")


# --------------------------------------------------------------------------- team membership


@dataclass(frozen=True, slots=True)
class ExistingTeamMembership:
    person_id: uuid.UUID
    valid_from: dt.datetime
    valid_to: dt.datetime | None

    @property
    def open(self) -> bool:
        return self.valid_to is None


def validate_team_membership(
    *,
    person_id: uuid.UUID,
    person_status: PersonStatus | str,
    existing: Iterable[ExistingTeamMembership],
) -> None:
    """BR-I-02 and BR-I-03.

    A Person may be in many teams, so there is deliberately no rule limiting that. What is limited
    is being in the *same* team twice at once: membership is time-bounded, so a second open row for
    the same person is not a second membership, it is an ambiguous one.
    """
    assert_assignable(person_status)
    if any(row.person_id == person_id and row.open for row in existing):
        raise DomainRuleViolation(
            "BR-I-03", "this person already has an open membership of this team"
        )


def validate_membership_end(valid_from: dt.datetime, ended_at: dt.datetime) -> None:
    """BR-I-03. Ending sets `valid_to`; it never deletes the row, so history stays queryable."""
    if ended_at < valid_from:
        raise DomainRuleViolation(
            "BR-I-03", "a membership cannot end before it began"
        )


# ----------------------------------------------------------------------- org membership


def validate_organization_membership(
    *, person_status: PersonStatus | str, existing_status: MembershipStatus | None
) -> None:
    """One membership per person per organization, and it is the row that is reactivated.

    The schema enforces uniqueness on `(org_id, person_id)`, so re-joining is an update rather than
    an insert. Saying so here means the service explains it instead of the database refusing it.
    """
    if existing_status is not None:
        raise DomainRuleViolation(
            "BR-I-04",
            f"this person already has a membership of this organization ({existing_status}); "
            "change its status rather than creating a second one",
        )
    assert_assignable(person_status)


MEMBERSHIP_TRANSITIONS: dict[MembershipStatus, frozenset[MembershipStatus]] = {
    MembershipStatus.ACTIVE: frozenset({MembershipStatus.SUSPENDED, MembershipStatus.ENDED}),
    MembershipStatus.SUSPENDED: frozenset({MembershipStatus.ACTIVE, MembershipStatus.ENDED}),
    MembershipStatus.ENDED: frozenset({MembershipStatus.ACTIVE}),
}


def validate_membership_transition(
    current: MembershipStatus, target: MembershipStatus
) -> None:
    if current == target:
        raise DomainRuleViolation("BR-I-04", f"this membership is already {target}")
    if target not in MEMBERSHIP_TRANSITIONS[current]:
        permitted = ", ".join(sorted(MEMBERSHIP_TRANSITIONS[current])) or "nothing"
        raise DomainRuleViolation(
            "BR-I-04", f"{current} may move to {permitted}, not {target}"
        )


# --------------------------------------------------------------------------- role assignment


@dataclass(frozen=True, slots=True)
class ExistingRoleAssignment:
    role: str
    scope_type: ScopeType
    scope_id: uuid.UUID | None
    revoked_at: dt.datetime | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


def validate_role_assignment(
    *,
    role: str,
    scope_type: ScopeType,
    scope_id: uuid.UUID | None,
    person_status: PersonStatus | str,
    existing: Iterable[ExistingRoleAssignment],
) -> None:
    """A grant is a scope plus a role, and the pair is what must be unique while it is live.

    Everything below a whole organization needs to say *which* one. A `department_lead` with no
    department is not a narrower grant, it is an unbounded one wearing a narrow name — which is the
    shape of authorization bug that is invisible until somebody reads a row.
    """
    if PersonStatus(person_status) is PersonStatus.DEPARTED:
        raise DomainRuleViolation(
            "BR-I-05", "a departed person cannot be granted a new role"
        )
    if scope_type is not ScopeType.ORGANIZATION and scope_id is None:
        raise DomainRuleViolation(
            "BR-G-01", f"a {scope_type} scoped role must name the {scope_type} it applies to"
        )
    if scope_type is ScopeType.ORGANIZATION and scope_id is not None:
        raise DomainRuleViolation(
            "BR-G-01", "an organization-scoped role covers the whole organization already"
        )
    for grant in existing:
        if (
            grant.active
            and grant.role == role
            and grant.scope_type is scope_type
            and grant.scope_id == scope_id
        ):
            raise DomainRuleViolation(
                "BR-G-01", "this person already holds that role in that scope"
            )


# --------------------------------------------------------------------------- external identity


@dataclass(frozen=True, slots=True)
class ExistingExternalIdentity:
    source_system: str
    external_id: str
    person_id: uuid.UUID


def validate_external_identity(
    *,
    source_system: str,
    external_id: str,
    confidence: int,
    existing: Iterable[ExistingExternalIdentity],
) -> None:
    """BR-I-07, and the half of BR-I-06 that can be checked without knowing the use.

    Uniqueness is per organization and per `(source_system, external_id)`: one phone number
    resolves to at most one person here. Two rows claiming the same handle is not a conflict to
    resolve later; it is an attribution the system cannot make at all.
    """
    if not source_system.strip():
        raise DomainRuleViolation("BR-I-07", "an external identity requires a source system")
    if not external_id.strip():
        raise DomainRuleViolation("BR-I-07", "an external identity requires an external id")
    if not 0 <= confidence <= 100:
        raise DomainRuleViolation("BR-I-06", "confidence is a percentage from 0 to 100")
    for mapping in existing:
        if mapping.source_system == source_system and mapping.external_id == external_id:
            raise DomainRuleViolation(
                "BR-I-07",
                f"{source_system}:{external_id} is already mapped to another person in this "
                "organization",
            )


def may_attribute(*, confidence: int, confirmed_at: dt.datetime | None) -> bool:
    """BR-I-06. Whether a mapping is strong enough to attribute ownership or authorship.

    Read-only and deliberately not enforced by a constraint: a weak mapping is a legitimate row that
    may suggest. What it may not do is decide, and that is a question asked at the point of use —
    Phase 3, when there is a channel to ask it about.
    """
    return confirmed_at is not None and confidence >= MIN_ATTRIBUTION_CONFIDENCE
