# Resolution Pack v1.1 — Binding Decisions

Issued by: Product Owner / Solution Architect · Applied to the document set: 2026-09-11
Status: **binding**. Resolves the Phase 1 blockers raised in the Decision Impact Report.

Supplements `decision-pack-v1.0.md`. Where the two differ in detail, v1.1 is later and wins.

---

## C-1 — MVP AI autonomy for the three borderline cases · RESOLVED

Ceiling remains Level 1–2. All three cases take **Level 1 Proposal semantics**.

**Evidence attachment.** AI may identify that Evidence should attach to an existing Work, Commitment,
Risk, Project or other entity, but must create a Proposal and must not persist the attachment in the
MVP. Flow: Event → AI interpretation → Evidence candidate → Proposal → human approval → Tool Gateway →
Application Service → persisted relationship.

**Risk narrative.** Deterministic monitors create or identify Risks by deterministic rules; that
detection is not an AI mutation. An AI-generated explanatory narrative for such a Risk is a proposed
mutation. Flow: deterministic detection → Risk exists → AI proposes narrative → Proposal → human
approval → narrative persisted.

**Read-side summaries.** AI may generate summaries on demand. A summary that is not persisted as
business state requires no Proposal. If it is persisted as a durable business record, or becomes part
of Work Core state, it is a mutation and follows normal Proposal/Approval policy.

**Principle.** Do not confuse *AI can calculate or explain something* with *AI is authorized to persist
that interpretation*. The first is allowed; the second requires the applicable autonomy policy.

## M-1 / ADR-0032 — WorkAssignment · RESOLVED, ACCEPTED

`WorkAssignment` is the **canonical source of truth** for Work-to-Person assignment. A Work may have
multiple assignments. Assignment must not be modelled primarily through a single
`work.assignee_person_id` field.

Minimum conceptual fields: `id`, `org_id`, `work_id`, `person_id`, `role`, `is_primary`, `assigned_at`,
`assigned_by`, `started_at`, `ended_at`, `status`, `created_at`, `updated_at`. Implementation fields may
be refined; the semantics are binding.

Roles in the MVP: **OWNER** (zero or one active per Work), **CONTRIBUTOR** (zero or more),
**REVIEWER** (zero or more).

A Work may exist with **no assignment**. This is required for AI capture: "Someone please check the IOC
API before Friday." must produce Work with no assignment, and optionally a Proposal to assign a
responsible person. The system must not invent an assignee.

No second mutable source of truth. If fast access to the primary owner is needed, use query
optimisation, a database view, a projection or a cached read model.

**AI rule.** If a person cannot be identified with sufficient confidence: do not invent a Person, do not
assign the Work. Create a Proposal or an unresolved attribution state instead.

## N-3 — Comments · RESOLVED

No separate `Comment` entity in the MVP. Comments are Events: `Event.type = COMMENT` with
`Event.origin = INTERNAL` where appropriate.

The Event type enum may be refined but must support at least: `EXTERNAL_MESSAGE`, `MANUAL_CAPTURE`,
`MEETING_NOTE`, `COMMENT`, `ATTACHMENT`, `SYSTEM_ACTIVITY`.

Comments are activity and provenance, not independent Work Core business entities. A separate Comment
aggregate is introduced only if future requirements demonstrate a need for threaded discussion,
mentions, reactions, subscriptions, moderation or rich collaborative discussion. All of that is
explicitly out of MVP scope.

## Optional Project semantics · CONFIRMED AND EXTENDED

`Work.project_id` remains nullable and Work without a Project is a valid first-class Work.

Do not create a General Project, a Default Project or an Unassigned Project to satisfy relational
structure.

**Reporting.** The system must support "All Work" split into **Project Work** and **Non-project Work**.
Project rollups must ignore non-project Work rather than force it into a synthetic Project.

**AI behaviour.** Failure to identify a Project is not an error. AI may suggest a Project when evidence
and confidence are sufficient, and must not invent Project membership. Valid result for "Anh xử lý việc
này trước thứ Sáu nhé.": Work, Project = NULL, Assignment = NULL or an identified Person, Deadline =
Friday. No synthetic Project.

## Consistency checks required by the pack

**Domain** — Work without Project; Work without Assignment; multiple assignments; at most one active
OWNER; WorkAssignment canonical.
**AI** — cannot invent a Person; cannot invent Project membership; Evidence attachment is Level 1; Risk
narrative persistence is Level 1; non-persisted summaries need no approval.
**Security** — every WorkAssignment is organization-scoped; approval authority derives from the
approving human; AI cannot borrow the authority of a person merely because they appear in an Event.
**Events** — Comment is an Event; internal Events do not recursively enter the external extraction
pipeline; DomainEvent remains distinct from Event.
**Reporting** — Project rollups exclude non-project Work; organization-wide reporting includes both.
