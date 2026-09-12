"""Request and response shapes for `/api/v1/work`.

The wire format is not the domain model and not the ORM row. Keeping them apart is what lets a
column be renamed without breaking a client, and — more importantly here — what keeps a field the
API never meant to expose from appearing in a response because somebody added it to a table.

Update requests use `exclude_unset` rather than nullable defaults: "leave the due date alone" and
"clear the due date" are different intentions, and a model that cannot tell them apart erases data
on every partial update. The services already speak that distinction (`commands.UNSET`); this is
where it enters the system.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.contexts.commitment.public import CommitmentStatus, DuePrecision
from app.contexts.identity.public import (
    MembershipStatus,
    PersonStatus,
    ScopeType,
    TeamRole,
    UnitStatus,
)
from app.contexts.intelligence.public import Decision as ApprovalDecision
from app.contexts.intelligence.public import ProposalKind, execution_deadline
from app.contexts.signal.public import (
    Assertion,
    EventType,
    EvidenceTarget,
    ParticipantRole,
    Sensitivity,
)
from app.contexts.work.public import (
    AssignmentRole,
    DependencyKind,
    MilestoneStatus,
    ProjectStatus,
    Visibility,
    WorkPriority,
    WorkStatus,
    WorkType,
)
from app.platform.authz import Action as PolicyAction
from app.platform.authz import Role as WorkosRole
from app.platform.authz.agent import AgentCapability, AutonomyMode
from app.platform.http.validation import CleanText


class WorkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: CleanText = Field(min_length=1)
    # Every one of these is optional, and that is the product decision rather than laxity
    # (ADR-0029, BR-W-07, BR-W-15). No API path may require a Project or an assignment.
    project_id: uuid.UUID | None = None
    milestone_id: uuid.UUID | None = None
    parent_work_id: uuid.UUID | None = None
    description: CleanText | None = None
    type: WorkType = WorkType.TASK
    priority: WorkPriority = WorkPriority.NORMAL
    due_date: dt.date | None = None
    #: Omit it to inherit the Project's level; naming one wider than the Project is refused
    #: (BR-W-19). Work with no Project defaults to `team`.
    visibility: Visibility | None = None


class WorkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: CleanText | None = Field(default=None, min_length=1)
    description: CleanText | None = None
    type: WorkType | None = None
    priority: WorkPriority | None = None
    due_date: dt.date | None = None
    project_id: uuid.UUID | None = None
    milestone_id: uuid.UUID | None = None
    parent_work_id: uuid.UUID | None = None


class WorkStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: WorkStatus
    #: Required when moving to `blocked` unless an active blocking dependency already exists
    #: (BR-W-04). The service decides; this only carries it.
    blocked_reason: CleanText | None = None


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    role: AssignmentRole = AssignmentRole.CONTRIBUTOR
    is_primary: bool = False


class WorkResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    milestone_id: uuid.UUID | None
    parent_work_id: uuid.UUID | None
    title: str
    description: str | None
    type: str
    status: str
    priority: str
    due_date: dt.date | None
    blocked_reason: str | None
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    visibility: str
    source: str
    created_by_person_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int

    @property
    def partition(self) -> str:
        return "non_project" if self.project_id is None else "project"


class WorkListItem(WorkResource):
    """Identical to the single-item shape.

    A narrower list projection would be an optimisation nobody has measured, and the first client to
    need a field the list omits would fetch each row individually — which is slower than sending it.
    """


class WorkList(BaseModel):
    items: list[WorkListItem]
    #: Null on the last page. Opaque: it encodes the sort key and clients must not parse it.
    next_cursor: str | None = None


class AssignmentResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    work_id: uuid.UUID
    person_id: uuid.UUID
    role: str
    is_primary: bool
    status: str
    assigned_at: dt.datetime
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    source: str
    created_by_person_id: uuid.UUID | None
    version: int


__all__ = [
    "AssignmentCreate",
    "AssignmentResource",
    "WorkCreate",
    "WorkList",
    "WorkListItem",
    "WorkResource",
    "WorkStatusChange",
    "WorkUpdate",
]


# --------------------------------------------------------------------------- project


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText = Field(min_length=1)
    #: BR-P-01: one of these two is required. Which one is the domain's rule to state, not the
    #: schema's, so both are optional here and the service refuses the empty pair with a rule id.
    owning_team_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    description: CleanText | None = None
    objective: CleanText | None = None
    lead_person_id: uuid.UUID | None = None
    sponsor_person_id: uuid.UUID | None = None
    start_date: dt.date | None = None
    target_date: dt.date | None = None
    visibility: Visibility = Visibility.ORGANIZATION


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText | None = Field(default=None, min_length=1)
    description: CleanText | None = None
    objective: CleanText | None = None
    owning_team_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    lead_person_id: uuid.UUID | None = None
    sponsor_person_id: uuid.UUID | None = None
    start_date: dt.date | None = None
    target_date: dt.date | None = None


class ProjectStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: ProjectStatus


class ProjectResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str | None
    objective: str | None
    owning_team_id: uuid.UUID | None
    department_id: uuid.UUID | None
    lead_person_id: uuid.UUID | None
    sponsor_person_id: uuid.UUID | None
    status: str
    start_date: dt.date | None
    target_date: dt.date | None
    actual_end_date: dt.date | None
    visibility: str
    source: str
    created_by_person_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class ProjectList(BaseModel):
    items: list[ProjectResource]
    next_cursor: str | None = None


# --------------------------------------------------------------------------- milestone


class MilestoneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText = Field(min_length=1)
    description: CleanText | None = None
    acceptance_criteria: CleanText | None = None
    target_date: dt.date | None = None
    order_index: int = 0


class MilestoneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText | None = Field(default=None, min_length=1)
    description: CleanText | None = None
    acceptance_criteria: CleanText | None = None
    target_date: dt.date | None = None
    order_index: int | None = None


class MilestoneStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: MilestoneStatus


class MilestoneResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    acceptance_criteria: str | None
    target_date: dt.date | None
    actual_date: dt.date | None
    status: str
    order_index: int
    created_by_person_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class MilestoneList(BaseModel):
    """Unpaginated. A Project with more milestones than fit on a page has a different problem."""

    items: list[MilestoneResource]


# --------------------------------------------------------------------------- dependency


class DependencyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocker_type: Literal["work", "milestone"]
    blocker_id: uuid.UUID
    blocked_type: Literal["work", "milestone"]
    blocked_id: uuid.UUID
    kind: DependencyKind = DependencyKind.BLOCKS
    rationale: CleanText | None = None


class DependencyResource(BaseModel):
    """Endpoints as type and id only.

    Deliberately no titles. A dependency tells you your work is waiting on `work:5f3a`; what
    `work:5f3a` says is a separate question with its own visibility answer (BR-W-18), and folding it
    in here would route around that.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    blocker_type: str
    blocker_id: uuid.UUID
    blocked_type: str
    blocked_id: uuid.UUID
    kind: str
    status: str
    rationale: str | None
    created_by_person_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class DependencyList(BaseModel):
    items: list[DependencyResource]


# --------------------------------------------------------------------------- owner


class OwnerAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    is_primary: bool = False


class AssignmentList(BaseModel):
    items: list[AssignmentResource]


# --------------------------------------------------------------------------- errors


class Problem(BaseModel):
    """RFC 9457, as this API emits it.

    Declared so the OpenAPI document describes the errors the application actually returns.
    Publishing FastAPI's default validation schema while returning problem+json would make the
    document a description of a different service — and a generated client would be built against
    the wrong one.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="Stable URN, e.g. urn:workos:error:rule-violation")
    title: str
    status: int
    detail: str
    rule: str | None = Field(
        default=None, description="Business rule id, present on rule violations"
    )


def _problem(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "model": Problem,
        "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
    }


#: Attached to every router. Every one of these is reachable on every authenticated route, so
#: documenting them per-endpoint would be the same list copied a dozen times and drifting.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _problem("Missing or malformed organization context, or an invalid cursor"),
    401: _problem("No bearer token, or one this application will not accept"),
    403: _problem("Authenticated, and not permitted this action on this resource"),
    404: _problem("No such resource is visible in this organization"),
    409: _problem("Another request with the same Idempotency-Key is in flight"),
    412: _problem("The If-Match precondition failed; re-read and reapply"),
    422: _problem("Request validation failed, or a business rule refused the change"),
    428: _problem("This request requires an If-Match header"),
}


# --------------------------------------------------------------------------- identity


class CurrentPrincipal(BaseModel):
    """The caller, as this organization sees them.

    `roles` is here so a client can render navigation that matches what the caller may do. It is
    guidance for the interface and nothing more: the backend re-decides every request, so a client
    that ignored this and showed every button would be refused rather than obeyed (contract §14).
    """

    person_id: uuid.UUID
    org_id: uuid.UUID
    display_name: str
    email: str | None
    roles: list[str]


class PersonResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    display_name: str
    email: str | None
    status: str
    timezone: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class PersonList(BaseModel):
    items: list[PersonResource]
    next_cursor: str | None = None


class TeamResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    department_id: uuid.UUID | None
    name: str
    lead_person_id: uuid.UUID | None
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class TeamList(BaseModel):
    items: list[TeamResource]
    next_cursor: str | None = None


class DepartmentResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    parent_department_id: uuid.UUID | None
    name: str
    lead_person_id: uuid.UUID | None
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class DepartmentList(BaseModel):
    items: list[DepartmentResource]
    next_cursor: str | None = None


# --------------------------------------------------------------------------- identity writes


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText | None = Field(default=None, min_length=1)
    timezone: CleanText | None = Field(default=None, min_length=1)


class OrganizationResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    status: str
    ai_enabled: bool
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class PersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: CleanText = Field(min_length=1)
    email: CleanText | None = None
    #: ADR-0036. Present for somebody who will sign in; absent for somebody only ever referenced,
    #: who can still own work and be named as a committer (BR-I-04).
    keycloak_subject: CleanText | None = None
    timezone: CleanText | None = None


class PersonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: CleanText | None = Field(default=None, min_length=1)
    email: CleanText | None = None
    keycloak_subject: CleanText | None = None
    timezone: CleanText | None = None


class PersonStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: PersonStatus


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText = Field(min_length=1)
    parent_department_id: uuid.UUID | None = None
    lead_person_id: uuid.UUID | None = None


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText | None = Field(default=None, min_length=1)
    parent_department_id: uuid.UUID | None = None
    lead_person_id: uuid.UUID | None = None


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText = Field(min_length=1)
    department_id: uuid.UUID | None = None
    lead_person_id: uuid.UUID | None = None


class TeamUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CleanText | None = Field(default=None, min_length=1)
    department_id: uuid.UUID | None = None
    lead_person_id: uuid.UUID | None = None


class UnitStatusChange(BaseModel):
    """Archiving a department or a team. Neither is ever deleted (BR-G-04)."""

    model_config = ConfigDict(extra="forbid")

    target: UnitStatus


class TeamMemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    role: TeamRole = TeamRole.MEMBER


class TeamMembershipResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    team_id: uuid.UUID
    person_id: uuid.UUID
    role: str
    valid_from: dt.datetime
    valid_to: dt.datetime | None
    created_by_person_id: uuid.UUID | None
    version: int


class TeamMembershipList(BaseModel):
    items: list[TeamMembershipResource]


class MembershipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID


class MembershipStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: MembershipStatus


class MembershipResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    person_id: uuid.UUID
    status: str
    joined_at: dt.datetime
    left_at: dt.datetime | None
    created_by_person_id: uuid.UUID | None
    version: int


class RoleGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: WorkosRole
    scope_type: ScopeType = ScopeType.ORGANIZATION
    #: Required for anything narrower than the organization: a department-scoped role that names no
    #: department is an unbounded grant wearing a narrow name.
    scope_id: uuid.UUID | None = None


class RoleAssignmentResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    person_id: uuid.UUID
    role: str
    scope_type: str
    scope_id: uuid.UUID | None
    granted_at: dt.datetime
    granted_by_person_id: uuid.UUID | None
    revoked_at: dt.datetime | None
    version: int


class RoleAssignmentList(BaseModel):
    items: list[RoleAssignmentResource]


class ExternalIdentityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: CleanText = Field(min_length=1)
    external_id: CleanText = Field(min_length=1)
    handle: CleanText | None = None
    confidence: int = Field(default=0, ge=0, le=100)


class ExternalIdentityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handle: CleanText | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)


class ExternalIdentityResource(BaseModel):
    """The external id is returned as stored.

    It is the thing being mapped, so a directory that hid it could not be reviewed. Reading is
    organization-wide by ADR-0037 for that reason; what is *not* organization-wide is changing it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    person_id: uuid.UUID
    source_system: str
    external_id: str
    handle: str | None
    confidence: int
    confirmed_at: dt.datetime | None
    confirmed_by_person_id: uuid.UUID | None
    version: int


class ExternalIdentityList(BaseModel):
    items: list[ExternalIdentityResource]


# --------------------------------------------------------------------------- capture (events)


class ParticipantInputModel(BaseModel):
    """Somebody named on an Event.

    `person_id` and `external_handle` are both optional and at least one is required (BR-E-12). The
    pairing is the point: a meeting has attendees the system can name, and a channel message has a
    number it cannot resolve yet, and both are participants.
    """

    model_config = ConfigDict(extra="forbid")

    role: ParticipantRole
    person_id: uuid.UUID | None = None
    external_handle: CleanText | None = None
    confidence: int = Field(default=0, ge=0, le=100)


class EventCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EventType
    occurred_at: dt.datetime
    #: Defaults to `web`, which is what a manual capture through this API is. A caller integrating a
    #: registered channel names its own key so that BR-E-02's dedup is scoped to that channel.
    source_system: CleanText = Field(default="web", min_length=1)
    source_ref: CleanText | None = None
    title: CleanText | None = None
    body_text: CleanText | None = None
    channel: CleanText | None = None
    #: BR-E-12. Recorded verbatim and never overwritten when the sender is later resolved.
    sender_external_id: CleanText | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL
    participants: list[ParticipantInputModel] = Field(default_factory=list, max_length=200)


class EventParticipantResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_id: uuid.UUID | None
    external_handle: str | None
    role: str
    match_confidence: int
    resolved_at: dt.datetime | None


class EventAttachmentResource(BaseModel):
    """An attachment's metadata. Never its bytes, and never its object key.

    The key is server-derived (ADR-0039) and publishing it would let a caller reason about the
    bucket layout, which is the one thing that must stay uninteresting.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID
    filename: str
    media_type: str
    size_bytes: int | None
    checksum: str | None
    status: str
    uploaded_by_person_id: uuid.UUID | None
    created_at: dt.datetime
    completed_at: dt.datetime | None
    version: int


class EventResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    type: str
    origin: str
    source_system: str
    source_ref: str | None
    content_hash: str
    channel: str | None
    sender_external_id: str | None
    occurred_at: dt.datetime
    observed_at: dt.datetime
    title: str | None
    body_text: str | None
    sensitivity: str
    participant_count: int
    processing_status: str
    #: BR-E-02. Set when this Event corrects one that arrived under the same external reference;
    #: the original is never touched.
    revision_of_event_id: uuid.UUID | None
    #: BR-E-13. Who wrote it down — not a claim about who authored the content.
    captured_by_person_id: uuid.UUID | None
    created_at: dt.datetime
    version: int


class EventDetail(EventResource):
    participants: list[EventParticipantResource] = Field(default_factory=list)
    attachments: list[EventAttachmentResource] = Field(default_factory=list)


class EventList(BaseModel):
    items: list[EventResource]
    next_cursor: str | None = None


class AttachmentStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: CleanText = Field(min_length=1)
    media_type: CleanText = Field(min_length=1)


class AttachmentTicketResource(BaseModel):
    """The attachment row plus a short-lived URL to write it (ADR-0039).

    The upload does not pass through this API. The client PUTs to `upload_url` and then calls the
    complete endpoint, at which point the size and checksum are read from the store rather than
    believed from the client.
    """

    attachment: EventAttachmentResource
    upload_url: str
    expires_at: dt.datetime


class AttachmentContentResource(BaseModel):
    attachment: EventAttachmentResource
    download_url: str
    expires_at: dt.datetime


# --------------------------------------------------------------------------- evidence


class TextLocatorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)


class AttachmentLocatorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_id: uuid.UUID


class EvidenceCreate(BaseModel):
    """A citation.

    The locator decides which half of the model applies: a text span requires a verbatim `excerpt`
    checked against the Event (BR-E-05), an attachment requires a `claim_summary` because a PDF has
    no span to quote (BR-E-14). Supplying both, or neither, is refused.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    target_type: EvidenceTarget
    target_id: uuid.UUID
    assertion: Assertion
    text_locator: TextLocatorModel | None = None
    attachment_locator: AttachmentLocatorModel | None = None
    excerpt: CleanText | None = None
    claim_summary: CleanText | None = None
    confidence: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def the_locator_decides_the_claim(self) -> EvidenceCreate:
        """One locator, and the matching half of the claim.

        A mismatched pair is refused rather than silently narrowed. Dropping a `claim_summary` a
        caller supplied alongside a text quote would store something other than what was sent, and
        a citation the caller cannot predict the shape of is not a citation they can rely on.
        """
        if (self.text_locator is None) == (self.attachment_locator is None):
            raise ValueError("exactly one of text_locator or attachment_locator is required")
        if self.text_locator is not None:
            if self.excerpt is None:
                raise ValueError("a text locator requires a verbatim excerpt (BR-E-05)")
            if self.claim_summary is not None:
                raise ValueError("a text citation quotes; it does not summarise (BR-E-05)")
        else:
            if self.claim_summary is None:
                raise ValueError("an attachment citation requires a claim summary (BR-E-14)")
            if self.excerpt is not None:
                raise ValueError("an attachment has no span to quote (BR-E-14)")
        return self


class EvidenceResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    event_id: uuid.UUID
    locator: dict[str, Any]
    excerpt: str | None
    claim_summary: str | None
    target_type: str
    target_id: uuid.UUID
    assertion: str
    confidence: int
    produced_by_type: str
    produced_by_id: uuid.UUID | None
    #: BR-E-06. A superseded citation is retained and visible; "we used to believe this" stays
    #: answerable, which is the point of superseding rather than editing.
    superseded_by_id: uuid.UUID | None
    created_at: dt.datetime
    version: int


class EvidenceList(BaseModel):
    items: list[EvidenceResource]


# --------------------------------------------------------------------------- commitment


class CommitmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: CleanText = Field(min_length=1)
    committed_by_person_id: uuid.UUID
    committed_to_person_id: uuid.UUID | None = None
    committed_to_team_id: uuid.UUID | None = None
    due_date: dt.date | None = None
    due_precision: DuePrecision = DuePrecision.VAGUE
    fulfilling_work_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    origin_event_id: uuid.UUID | None = None
    confidence: int = Field(default=0, ge=0, le=100)


class CommitmentUpdate(BaseModel):
    """The due date is absent on purpose: moving a deadline is a renegotiation (BR-C-07)."""

    model_config = ConfigDict(extra="forbid")

    statement: CleanText | None = Field(default=None, min_length=1)
    due_precision: DuePrecision | None = None
    fulfilling_work_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


class CommitmentStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: CommitmentStatus
    #: Required when renegotiating (BR-C-07), meaningless otherwise.
    new_due_date: dt.date | None = None


class CommitmentResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    statement: str
    committed_by_person_id: uuid.UUID
    committed_to_person_id: uuid.UUID | None
    committed_to_team_id: uuid.UUID | None
    due_date: dt.date | None
    due_precision: str
    status: str
    fulfilling_work_id: uuid.UUID | None
    project_id: uuid.UUID | None
    origin_event_id: uuid.UUID | None
    confidence: int
    acknowledged_at: dt.datetime | None
    previous_due_date: dt.date | None
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class CommitmentList(BaseModel):
    items: list[CommitmentResource]
    next_cursor: str | None = None


# --------------------------------------------------------------------------- proposal


class ProposedChangeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: CleanText = Field(min_length=1)
    current_value: Any = None
    proposed_value: Any = None


class ProposalCreate(BaseModel):
    """A reviewable mutation that has not happened.

    `tool` and `arguments` are the action: fully resolved, nothing left to infer at execution. The
    tool must exist in the Gateway's registry and the arguments must fit it, checked now so that a
    Proposal nobody could execute never reaches a reviewer (ADR-0042).
    """

    model_config = ConfigDict(extra="forbid")

    kind: ProposalKind
    target_type: CleanText = Field(min_length=1)
    summary: CleanText = Field(min_length=1)
    tool: CleanText = Field(min_length=1)
    tool_version: CleanText = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    routed_to_person_id: uuid.UUID
    target_id: uuid.UUID | None = None
    reason: CleanText | None = None
    confidence: int = Field(default=0, ge=0, le=100)
    source_event_id: uuid.UUID | None = None
    evidence_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    changes: list[ProposedChangeModel] = Field(default_factory=list, max_length=100)


class ProposalRevise(BaseModel):
    """BR-PR-02. Produces a new Proposal; the original becomes `superseded`."""

    model_config = ConfigDict(extra="forbid")

    summary: CleanText | None = None
    reason: CleanText | None = None
    arguments: dict[str, Any] | None = None
    changes: list[ProposedChangeModel] | None = None


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    #: BR-PR-06. Present only for `approved_with_edits`; these become the approved action, and
    #: therefore what the hash is taken over.
    edited_arguments: dict[str, Any] | None = None
    rejection_reason: CleanText | None = None


class ProposalResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    target_type: str
    target_id: uuid.UUID | None
    summary: str
    reason: str | None
    confidence: int
    action: dict[str, Any]
    #: The digest execution matches against (ADR-0041). Published so a client can show that what it
    #: is approving is what it was shown.
    action_hash: str
    source_event_id: uuid.UUID | None
    supersedes_proposal_id: uuid.UUID | None
    raised_by_person_id: uuid.UUID | None
    routed_to_person_id: uuid.UUID
    status: str
    reviewed_by_person_id: uuid.UUID | None
    reviewed_at: dt.datetime | None
    rejection_reason: str | None
    expires_at: dt.datetime
    created_at: dt.datetime
    version: int


class ProposedChangeResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field_path: str
    current_value: dict[str, Any] | None
    proposed_value: dict[str, Any] | None


class ProposalDetail(ProposalResource):
    changes: list[ProposedChangeResource] = Field(default_factory=list)
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)


class ProposalList(BaseModel):
    items: list[ProposalResource]
    next_cursor: str | None = None


class ApprovalRecordResource(BaseModel):
    """Immutable except for the execution outcome, which is written once."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    proposal_id: uuid.UUID
    approver_person_id: uuid.UUID
    decision: str
    approved_action: dict[str, Any]
    approved_action_hash: str
    edits: dict[str, Any] | None
    decided_at: dt.datetime
    execution_status: str
    resulting_entity_type: str | None
    resulting_entity_id: uuid.UUID | None
    executed_at: dt.datetime | None
    execution_error: str | None
    version: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def execution_expires_at(self) -> dt.datetime:
        """When this approval stops authorising anything (BR-AI-22, ADR-0051).

        Derived from `decided_at`, which is immutable, rather than stored — so the value is the
        same on every read and there is no second source of truth to drift. Published because a
        client showing an approval should be able to show its deadline.
        """
        return execution_deadline(self.decided_at)


class ExecutionResult(BaseModel):
    approval: ApprovalRecordResource
    entity_type: str
    entity_id: uuid.UUID


# --------------------------------------------------------------------------- agent


class AIInteractionResource(BaseModel):
    """One AI run.

    No prompt body, no model response and no credential — the columns do not exist. What is here is
    enough to answer which run this was, under whose authority, with which model and pinned prompt,
    and what came out (BR-PR-08).
    """

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    trigger_type: str
    trigger_ref: uuid.UUID | None
    #: BR-AI-03. The human whose authority the run borrowed.
    principal_person_id: uuid.UUID | None
    agent_identity: str
    runtime: str
    provider: str
    model: str
    model_version: str
    prompt_id: str
    prompt_version: str
    tool_manifest_version: str
    input_refs: dict[str, Any]
    status: str
    started_at: dt.datetime
    finished_at: dt.datetime | None
    latency_ms: int | None
    token_usage: dict[str, Any] | None
    output_summary: dict[str, Any] | None
    error: str | None
    version: int


class ToolCallResource(BaseModel):
    """One call the runtime made. Denials are recorded and returned like any other."""

    model_config = ConfigDict(from_attributes=True)

    sequence: int
    tool_name: str
    tool_version: str
    arguments_redacted: dict[str, Any]
    authorization_result: str
    outcome: str
    target_entity_type: str | None
    target_entity_id: uuid.UUID | None
    error: str | None
    created_at: dt.datetime


class AIInteractionDetail(AIInteractionResource):
    tool_calls: list[ToolCallResource] = Field(default_factory=list)


class AIInteractionList(BaseModel):
    items: list[AIInteractionResource]


class AnalysisResource(BaseModel):
    """What one Level 1 run produced. Identifiers, so a reviewer can go and look."""

    ai_interaction_id: uuid.UUID
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    proposal_ids: list[uuid.UUID] = Field(default_factory=list)
    #: BR-AI-09. Spans the model produced that were below threshold and became nothing.
    low_confidence: int = 0


# --------------------------------------------------------------------------- capability policy


class CapabilityPolicyResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    capability: str
    entity_type: str
    action: str
    mode: str
    reason: str | None
    decided_by_person_id: uuid.UUID | None
    updated_at: dt.datetime
    version: int


class CapabilityPolicyList(BaseModel):
    """Only the cells an organization has decided.

    An absent cell is `off` (ADR-0047) and is deliberately not rendered as a row: listing every
    possible combination with a default would make the decided ones hard to see, which is the
    opposite of what an autonomy policy is read for.
    """

    items: list[CapabilityPolicyResource]


class CapabilityPolicySet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: AgentCapability
    entity_type: CleanText = Field(min_length=1)
    action: PolicyAction
    mode: AutonomyMode
    #: What a reviewer actually wants six months later.
    reason: CleanText | None = None
