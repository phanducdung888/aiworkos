"""Where a proposed link may come from (ADR-0073, BR-AI-39).

CP27 opened the arguments and CP29 found what that was worth: 26 of 32 Work items in the live
organization belong to no Project, 9 of 14 Commitments fulfil no Work. Every link that exists was
made by hand. The reason is narrower than "the AI was told not to" — the runtime is never shown a
Project or a Work item, so it could not name one if it wanted to.

This module produces the set it is allowed to name, and the set has one property that does all the
work: **every member is there because somebody approved a row, not because a string scored well.**

Three rules, each a join:

1. this Event's own analysis produced a Proposal a person approved, which executed into a Work item;
2. another Event in the same conversation did (BR-E-19, `thread_ref`);
3. a candidate Work item's Project is a candidate Project.

**The chain is Proposal → ApprovalRecord → entity, and not Evidence.** The obvious join — "Evidence
from this Event targets that Work" — does not work in this system, and finding out why was the
useful part of writing this: `_create_evidence` sets `target_id` to a fresh `uuid.uuid4()` at
analysis time, because the entity does not exist until somebody approves the Proposal, and Evidence
is immutable (BR-E-06) so nothing ever rebinds it. Provenance is walked through the approval instead
(ADR-0056), and that is the chain with real identifiers in it. It is also the stronger claim: an
ApprovalRecord is a person deciding, where an Evidence row is the system citing.

That is deliberately not similarity search. CP14 removed title-similarity reasoning from the
commitment path and ADR-0071 rejected it again, both times for the same reason: a promise is not a
Work item, and searching one corpus for the other is a category error that reads as working. What
this proposes instead is a Work item the system already knows is the subject of this conversation.
The claim is "this message belongs to the conversation that produced that work item", and its
evidence is an approval, not a score.

**The empty set is the normal case at first.** A new organization has no approved links, so nothing
is a candidate, so the Proposal arrives with the fields empty and a reason — which is BR-AI-17
behaving correctly, not the resolver failing. The graph is learned from approvals and compounds;
it is not asserted on day one. Stated here because "the automation does nothing" is otherwise read
as a defect on the first run.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.contexts.signal.public as signal
import app.contexts.work.public as work
from app.contexts.intelligence.models import ApprovalRecord, Proposal
from app.platform.authz import Principal

#: Why a Project is a candidate. The Work reasons come from Signal (`THIS_EVENT`, `SAME_THREAD`);
#: this is the only reason this module adds, and it is derived rather than observed — the Project
#: was never evidence for anything, it simply owns a Work item that was.
OWNS_CANDIDATE_WORK = "owns_candidate_work"

#: A conversation that has been attached to many things is a conversation the resolver has nothing
#: useful to say about. Showing twenty candidates is not more helpful than showing none; it moves
#: the same search back to the reviewer while implying the system had an opinion.
MAX_CANDIDATES = 5


@dataclasses.dataclass(frozen=True, slots=True)
class WorkCandidate:
    id: uuid.UUID
    title: str
    status: str
    #: `signal.THIS_EVENT` or `signal.SAME_THREAD`.
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class ProjectCandidate:
    id: uuid.UUID
    title: str
    reason: str
    #: The Work item that brought it in. A reviewer asking "why this project?" is really asking
    #: which work item vouched for it.
    via_work_id: uuid.UUID


@dataclasses.dataclass(frozen=True, slots=True)
class LinkCandidates:
    """What an analysis of one Event is permitted to link to."""

    work: tuple[WorkCandidate, ...] = ()
    projects: tuple[ProjectCandidate, ...] = ()

    def allows_work(self, work_id: uuid.UUID) -> bool:
        return any(candidate.id == work_id for candidate in self.work)

    def allows_project(self, project_id: uuid.UUID) -> bool:
        return any(candidate.id == project_id for candidate in self.projects)

    def reason_for_work(self, work_id: uuid.UUID) -> str | None:
        return next((c.reason for c in self.work if c.id == work_id), None)

    def reason_for_project(self, project_id: uuid.UUID) -> str | None:
        return next((c.reason for c in self.projects if c.id == project_id), None)

    @property
    def is_empty(self) -> bool:
        return not self.work and not self.projects


def candidates_for_event(
    session: Session,
    principal: Principal,
    *,
    event_id: uuid.UUID,
) -> LinkCandidates:
    """The candidate set for one Event, read through the caller's own visibility.

    Two filters apply and both are filters rather than checks: Signal reports only conversation
    members this principal may read, and Work resolves only identifiers this principal may read. A
    Work item that survives both is one the caller could have found by browsing, so naming it in a
    Proposal discloses nothing they did not already have.
    """
    related = signal.events_in_thread(session, principal, event_id=event_id)
    if not related:
        return LinkCandidates()

    reason_by_event = dict(related)
    rows = session.execute(
        select(Proposal.source_event_id, ApprovalRecord.resulting_entity_id)
        .join(ApprovalRecord, ApprovalRecord.proposal_id == Proposal.id)
        .where(
            Proposal.org_id == principal.org_id,
            ApprovalRecord.org_id == principal.org_id,
            Proposal.source_event_id.in_([event for event, _ in related]),
            # Approved and actually carried out. A rejected decision is a person saying no, and an
            # approval that never executed produced nothing to link to.
            ApprovalRecord.decision == "approved",
            ApprovalRecord.execution_status == "executed",
            ApprovalRecord.resulting_entity_type == "work",
            ApprovalRecord.resulting_entity_id.is_not(None),
        )
        .order_by(ApprovalRecord.resulting_entity_id)
    ).all()
    if not rows:
        return LinkCandidates()

    # This Event's own work first, then the conversation's. Deduplicated by Work item, keeping the
    # stronger reason: one Work item approved from two messages in the same thread is one candidate.
    reason_by_work: dict[uuid.UUID, str] = {}
    for source_event_id, work_id in rows:
        reason = reason_by_event.get(source_event_id, signal.SAME_THREAD)
        if reason_by_work.get(work_id) != signal.THIS_EVENT:
            reason_by_work[work_id] = reason
    ordered_ids = sorted(
        reason_by_work, key=lambda i: (reason_by_work[i] != signal.THIS_EVENT, str(i))
    )

    references = work.work_references(
        session, principal, work.reach_of(session, principal), ordered_ids
    )
    by_id = {reference.id: reference for reference in references}
    ordered = [by_id[work_id] for work_id in ordered_ids if work_id in by_id]

    candidates = tuple(
        WorkCandidate(
            id=reference.id,
            title=reference.title,
            status=reference.status,
            reason=reason_by_work[reference.id],
        )
        for reference in ordered[:MAX_CANDIDATES]
    )

    projects: list[ProjectCandidate] = []
    seen: set[uuid.UUID] = set()
    for reference in ordered[:MAX_CANDIDATES]:
        if reference.project_id is None or reference.project_id in seen:
            continue
        seen.add(reference.project_id)
        projects.append(
            ProjectCandidate(
                id=reference.project_id,
                # `work_references` returns the name only when the join through readable work found
                # it. A Project named but not readable keeps its identifier and loses its name,
                # which is the narrower of the two things to disclose.
                title=reference.project_name or "",
                reason=OWNS_CANDIDATE_WORK,
                via_work_id=reference.id,
            )
        )

    return LinkCandidates(work=candidates, projects=tuple(projects))
