# Business Rules — AI WorkOS

Version 0.1 · Status: proposed

Rules are numbered and stable. Code, tests and review comments cite rule ids. A rule that is not
testable is not a rule; it is a preference, and belongs in the constitution instead.

Legend: **[INV]** invariant enforced at write time · **[TRN]** state transition constraint ·
**[POL]** policy, configurable per organization · **[DRV]** derived value definition.

---

## 1. Global rules

| # | Rule | Type |
|---|---|---|
| BR-G-01 | Every organization-owned entity belongs to exactly one Organization and carries `org_id`. No row may reference an entity in another organization. Scoped entities: Department, Team, Person, Project, Milestone, Work, WorkAssignment, Commitment, Dependency, Risk, Decision, Event, Evidence, Proposal, ApprovalRecord, Notification, AIInteraction, and all audit records. | INV |
| BR-G-01a | Tenant isolation is enforced twice: in application authorization and by PostgreSQL RLS. Neither substitutes for the other. AI-originated actions are subject to both, identically. | INV |
| BR-G-02 | Every mutation records an Actor and writes an AuditEntry in the same transaction as the change. | INV |
| BR-G-03 | Audit entries and Evidence are never updated or deleted by application code. | INV |
| BR-G-04 | Entities with Evidence or audit history are never hard-deleted. Removal is a lifecycle state (`cancelled`, `rejected`, `withdrawn`, `departed`). | INV |
| BR-G-05 | All timestamps are stored in UTC. Dates that carry human meaning (due dates, target dates) are stored as dates and interpreted in the timezone of the owning Person, else the Project, else the Organization. | INV |
| BR-G-06 | Concurrent edits to the same entity are resolved by optimistic concurrency. A write with a stale version is rejected, never merged. | INV |
| BR-G-07 | With `organization.ai_enabled = false`, no AI component may run and no AI-originated Proposal or execution may occur. All human paths continue to function, including web capture, Events, Evidence and manual Work Core operation. | POL |
| BR-G-08 | Derived fields (health, severity, staleness, load) are never writable through any API, human or AI. | INV |

## 2. Identity & Organization

| # | Rule | Type |
|---|---|---|
| BR-I-01 | Department hierarchy is a tree: no cycles, single parent, max depth 5. | INV |
| BR-I-02 | A Team belongs to at most one Department. A Person may belong to many Teams. | INV |
| BR-I-03 | Team membership is time-bounded. Ending membership never deletes the record; it sets `to`. | INV |
| BR-I-04 | A Person may exist without a Keycloak subject. Such a Person cannot authenticate but can be referenced as owner, assignee or committer. | INV |
| BR-I-05 | A Person marked `departed` cannot receive a new active `WorkAssignment` or be recorded as making a new Commitment. Existing assignments and records are retained, with active assignments ended rather than deleted. | INV |
| BR-I-06 | An ExternalIdentity mapping with `confidence < 0.90` or `confirmed_at is null` must not be used to attribute ownership, assignment or commitment authorship. It may be used to suggest. | POL |
| BR-I-07 | One ExternalIdentity per `(source_system, external_id)` per organization. | INV |

## 3. Project and Milestone

| # | Rule | Type |
|---|---|---|
| BR-P-01 | A Project must have a name and an owning Team or Department. | INV |
| BR-P-02 | `target_date >= start_date` when both are set. | INV |
| BR-P-03 | A Project may only move to `completed` when all its Milestones are `achieved`, `missed` or `cancelled`, and no Work in it is `in_progress` or `blocked`. | TRN |
| BR-P-04 | Cancelling a Project cancels its open Milestones and Work, recording the cascade in audit with the originating action. | TRN |
| BR-P-05 | A Milestone belongs to exactly one Project and cannot be moved between Projects. | INV |
| BR-P-06 | Work may reference a Milestone only within its own Project. | INV |
| BR-P-07 | A Milestone becomes `missed` automatically when `target_date` has passed and status is not `achieved` or `cancelled`. This is a derived transition performed by the monitor, attributed to the system actor. | TRN |
| BR-P-07a | **No synthetic Projects.** The system must not create a General, Default, Unassigned, Inbox or Miscellaneous Project to give unparented Work a parent, whether by migration, seed data, application default or AI proposal. | INV |
| BR-P-08 | Project `health` is derived, never set: `off_track` if any hard-blocking dependency chain reaches an unachieved milestone past target; `at_risk` if an open critical/high Risk is scoped to it, or >20% of open Work is overdue; else `on_track`. Thresholds are organization policy. | DRV |
| BR-P-09 | **Project visibility narrows read access and never widens it**, evaluated in addition to role reach exactly as BR-W-18 is for Work. `organization` — no further narrowing. `department` — additionally requires reach over the Project's Department, or over the Department of its owning Team. `team` — additionally requires membership of the owning Team, or department reach over that Team. `restricted` — no role reach suffices. At every level a Project is readable by its lead, its sponsor, the Person who created it, `org_admin` and `auditor`; that set is the floor, and `restricted` is exactly the floor. A Milestone carries no visibility of its own and takes its Project's, since it cannot exist apart from it (BR-P-05). | INV |

## 4. Work

| # | Rule | Type |
|---|---|---|
| BR-W-01 | Work requires a non-empty title. | INV |
| BR-W-02 | Work created by a human starts in `todo`. Work originating from AI exists as a Proposal until approved; on approval it is created in `todo`. The `proposed` Work status therefore applies only to human-drafted work, and AI candidates live in the Proposal table rather than as `proposed` Work rows. | TRN |
| BR-W-03 | Allowed transitions: `proposed → todo \| rejected`; `todo → in_progress \| cancelled`; `in_progress → blocked \| done \| cancelled`; `blocked → in_progress \| cancelled`; `done → in_progress` (reopen). All others rejected. | TRN |
| BR-W-04 | Moving to `blocked` requires either an active blocking Dependency or a free-text reason. | TRN |
| BR-W-05 | Work cannot be `done` while it has an active `blocks` Dependency where it is the blocked side, or while any child Work is open. | TRN |
| BR-W-06 | Work hierarchy depth is at most 3 (`parent → child → grandchild`). No cycles. | INV |
| BR-W-07 | **Project is optional on Work (ADR-0029).** Work with `project_id = null` is a fully functional record: it may be assigned, scheduled, blocked, completed, depended upon, and linked to a Commitment. Only Milestone linkage requires a Project. No API or UI path may require a Project to create Work. | INV |
| BR-W-07a | AI may suggest a Project for unparented Work when evidence supports it, as a Proposal. It must never infer, default or invent project membership (Decision Pack Rule 4). | POL |
| BR-W-08 | Setting `status = done` sets `completed_at`; reopening clears it and records both transitions in audit. | INV |
| BR-W-09 | Work belonging to a Project may move to `in_progress` only if that Project is `active` or `on_hold`. Work with no Project is unconstrained by this rule. | TRN |
| BR-W-10 | `last_signal_at` is updated whenever new Evidence targets the Work. Work is `stale` when `last_signal_at` is older than the organization's staleness window (default 14 days) and status is `in_progress`. | DRV |
| BR-W-11 | Assigning Work to a Person requires that Person to be `active` and in the same organization. | INV |
| BR-W-12 | **`WorkAssignment` is the canonical source of truth for assignment** (ADR-0032). Work carries no `assignee_person_id` column and no collaborator array. Any fast-access owner lookup is a view, projection or cached read model derived from `WorkAssignment`, never a writable column. | INV |
| BR-W-13 | Roles are `OWNER`, `CONTRIBUTOR`, `REVIEWER`. A Work may have **zero or one** active `OWNER`, **zero or more** active `CONTRIBUTOR`, and **zero or more** active `REVIEWER` assignments. The single-active-OWNER constraint is enforced by a partial unique index, not by application logic alone. | INV |
| BR-W-14 | Ending an assignment sets `ended_at` and `status = ended`. Assignment rows are never deleted or overwritten, so assignment history stays queryable and attributable. | INV |
| BR-W-15 | **Work may exist with no assignment**, and that is a valid steady state, not an error or a temporary condition to be corrected. No API, UI or service path may require an assignment to create Work. | INV |
| BR-W-16 | At most one active assignment per Work may carry `is_primary = true`. | INV |
| BR-W-17 | `started_at` is set on the first transition of a Work to `in_progress` and is never cleared, because it records when the work actually began rather than when it was most recently worked on. Reopening after `done` leaves `started_at` untouched and clears `completed_at` (BR-W-08). | INV |
| BR-W-18 | **Work visibility narrows read access and never widens it.** It is evaluated in addition to role reach, not instead of it: a principal must satisfy both (security-model §3.2). `organization` — no further narrowing. `department` — additionally requires that the Work's Project sits in a Department the principal leads, or is owned by a Team in one. `team` — additionally requires membership of the Team owning the Work's Project, or department reach over that Team. `restricted` — no role reach suffices. At **every** level a Work is readable by anyone holding an active `WorkAssignment` on it, by the Person who created it, and by `org_admin` and `auditor`; that set is the floor, and `restricted` is exactly the floor. Work with no Project has neither Team nor Department, so `team` and `department` on such Work resolve to the floor as well — the default `team` value is meaningless for it by construction (ADR-0029), and resolving it downward is the only reading that does not silently publish unparented Work to the whole organization. | INV |
| BR-W-19 | **Work belonging to a Project inherits that Project's visibility and is never wider than it.** On creation, Work with a Project takes the Project's level unless the caller names one explicitly, and an explicit level is accepted only if it is narrower than or equal to the Project's; wider is refused. Narrowing a Project's visibility narrows every Work of that Project that was wider, in the same transaction, with the originating action recorded in audit. Widening a Project's visibility does **not** widen its Work: read access is granted deliberately, never as a side effect. Work belonging to no Project is outside this rule and keeps its own default (ADR-0029). Order, widest to narrowest: `organization` > `department` > `team` > `restricted`. | INV |

## 5. Dependency

| # | Rule | Type |
|---|---|---|
| BR-D-01 | Endpoints must be distinct and in the same organization. | INV |
| BR-D-02 | The graph of `active` dependencies of kind `blocks` must remain acyclic. A write creating a cycle is rejected with the offending path returned. | INV |
| BR-D-03 | A Dependency becomes `resolved` automatically when its blocker reaches `done`/`achieved`. That makes the blocked item *eligible* to proceed; the system does not change its status (BR-AI-29). When the last active blocker is resolved and the Work is still `blocked`, MON-007a detects it and notifies. | TRN |
| BR-D-04 | Cross-project dependencies are allowed and flagged. Cross-organization dependencies are rejected. | INV |
| BR-D-05 | A Dependency whose blocker is cancelled is `withdrawn`, not silently deleted. | TRN |
| BR-D-06 | Duplicate dependencies with the same endpoints and kind are rejected as conflicts, returning the existing one. | INV |

## 6. Commitment

| # | Rule | Type |
|---|---|---|
| BR-C-01 | A Commitment requires a statement and a `committed_by` Person. | INV |
| BR-C-02 | `committed_to_person_id` and `committed_to_team_id` are mutually exclusive; both may be null (a promise made to the room). | INV |
| BR-C-03 | Every Commitment has at least one Evidence row when its source is AI. Commitments entered by a human need none. | INV |
| BR-C-04 | Allowed transitions: `captured → open \| disputed \| withdrawn`; `open → fulfilled \| missed \| renegotiated \| cancelled`; `renegotiated → open`; `missed → fulfilled` (late completion). | TRN |
| BR-C-05 | A Commitment marked `disputed` is retained with all its Evidence. Disputes are never deleted and are visible to both parties. | INV |
| BR-C-06 | A Commitment becomes `missed` when `due_date` has passed, status is `open`, and `due_precision` is `exact` or `week`. Vague-precision commitments are never auto-missed; they age into a review prompt instead. | TRN |
| BR-C-07 | Renegotiation preserves the prior due date in the audit trail and requires a new due date. | TRN |
| BR-C-08 | Linking a Commitment to fulfilling Work does not merge them. Completing the Work proposes, but does not force, fulfilment of the Commitment. | POL |
| BR-C-09 | Only the committer, the recipient, or a lead in their management chain may change a Commitment's status, except for system-driven `missed`. | INV |
| BR-C-10 | AI may never mark a Commitment `fulfilled` without an ApprovalRecord. Fulfilment always requires explicit human approval (BR-AI-18). | POL |

## 7. Risk

| # | Rule | Type |
|---|---|---|
| BR-R-01 | Likelihood and impact are integers 1–5. `severity = likelihood × impact`, banded: 1–4 low, 5–9 medium, 10–14 high, 15–25 critical. | DRV |
| BR-R-02 | A Risk must reference exactly one scope target that exists in the same organization. | INV |
| BR-R-03 | A Risk cannot remain `open` for longer than the organization's unowned-risk window (default 3 days) without an owner; it is escalated to the scope target's lead. | POL |
| BR-R-04 | Allowed transitions: `open → mitigating \| accepted \| closed \| realized`; `mitigating → closed \| realized \| accepted`; `accepted → realized \| closed`; `closed → open` (reopen with reason). | TRN |
| BR-R-05 | `accepted` requires a rationale. `closed` requires either a rationale or a resolving Evidence link. | TRN |
| BR-R-06 | A monitor-detected Risk with the same `monitor_rule_id` and scope target as an existing open Risk updates that Risk rather than creating a new one. | INV |
| BR-R-07 | A Risk whose scope target is cancelled or completed is auto-closed with reason `scope_ended`. | TRN |
| BR-R-08 | All AI-raised Risks are Proposals (BR-AI-16). Risks created by a deterministic monitor are not AI output and are created directly by the system actor. | POL |

## 8. Decision

| # | Rule | Type |
|---|---|---|
| BR-DE-01 | A Decision requires title, statement and rationale before reaching `accepted`. | TRN |
| BR-DE-02 | An `accepted` Decision is immutable except for `status` and `superseded_by`. Changes of substance create a new Decision that supersedes it. | INV |
| BR-DE-03 | Supersession is a chain, not a graph: a Decision is superseded by at most one Decision, and cycles are rejected. | INV |
| BR-DE-04 | Marking a Decision `superseded` requires a superseding Decision id. | TRN |
| BR-DE-05 | Decisions with `reversibility = irreversible` require a named decider and at least one Evidence link or explicit "recorded manually" attestation. | TRN |
| BR-DE-06 | AI may propose Decisions. Setting a Decision to `accepted` requires a human, either directly or through an ApprovalRecord naming that exact action. | POL |

## 9. Event and Evidence

| # | Rule | Type |
|---|---|---|
| BR-E-01 | Events are immutable after `received`, except for `processing_status`, participant resolution and retention fields. | INV |
| BR-E-02 | Ingestion is idempotent on `(org_id, source_system, source_ref)`. A repeat returns the existing Event. A differing `content_hash` for the same ref creates a revision Event that references the original. | INV |
| BR-E-03 | Every Event must have either `body_text` or a `raw_payload_uri`. | INV |
| BR-E-04 | Evidence must reference an Event and a target entity in the same organization. | INV |
| BR-E-05 | Evidence `excerpt` must be a verbatim substring of the Event's `body_text` at the recorded locator at the time of creation. Paraphrase is not evidence. | INV |
| BR-E-06 | Evidence is immutable. Corrections create new Evidence and set `superseded_by` on the old row. | INV |
| BR-E-07 | Deleting an Event under retention policy soft-deletes it, purges the raw payload, retains excerpts already captured in Evidence, and records the purge in audit. | POL |
| BR-E-08 | Events classified `restricted` are visible only to their participants and organization auditors, and may be excluded from AI processing by policy. | POL |
| BR-E-09 | An Event may not be ingested with `occurred_at` more than 24 hours in the future. | INV |
| BR-E-10 | Every Event carries `origin`. `external` Events come from the web capture surface or a registered channel adapter. `internal` Events are projected from allow-listed DomainEvents (§9a) and carry `origin_domain_event_id`. | INV |
| BR-E-11 | **Loop prevention.** Only Events with `origin = external` are queued for AI extraction. Internal Events are readable as AI context and usable as Evidence, but never trigger a capability. Without this rule, an AI-proposed Work item becomes a DomainEvent, becomes an Event, and is re-extracted. | INV |
| BR-E-12 | Channel-captured Events record `sender_external_id` verbatim. Resolution to a Person is a separate, confidence-scored step (BR-I-06) and never overwrites the raw sender identifier. | INV |
| BR-E-13 | An Event captured through the web surface is attributed to the authenticated user as *capturer*, which is distinct from the *authors* of its content. Pasting a colleague's message does not make the paster its author. | INV |
| BR-E-15 | **Comments are Events** (ADR-0033). A comment is `Event.type = COMMENT` with `origin = INTERNAL`. There is no `Comment` entity, no threading, no mentions, no reactions, no subscriptions and no moderation in the MVP. | INV |
| BR-E-16 | Because `COMMENT` Events are internal-origin, BR-E-11 currently excludes them from AI extraction. Whether that is the intended product behaviour is **open (N-4)** and must be answered before comments are shipped as a capture surface. Until then, no extraction path may read them. | POL |
| BR-E-14 | Attachments are stored against the Event and referenced by Evidence via an `{attachment_id}` locator. Verbatim excerpt verification (BR-E-05) does not apply to binary attachments; such Evidence carries a `claim_summary` and no `excerpt`. | INV |

## 9a. DomainEvent projection into internal Events

Allow-listed only. Any DomainEvent not in this table produces no Event.

All internal projections use `Event.type = SYSTEM_ACTIVITY` with `origin = INTERNAL`.

| DomainEvent | Projects | Rationale |
|---|---|---|
| `WorkStatusChanged`, `WorkCompleted` | yes | Progress belongs in the timeline and in rollups |
| `WorkAssigned`, `WorkAssignmentEnded` | yes | Ownership history is organizational activity |
| `CommitmentCreated`, `CommitmentAcknowledged`, `CommitmentCompleted`, `CommitmentMissed` | yes | Accountability trail |
| `DecisionAccepted` | yes | Decisions must be findable from the timeline |
| `RiskRaised`, `RiskStatusChanged` | yes | Risk history |
| `NotificationSent` | yes, only when the organization enables it | High volume |

`ProposalRaised`, `ProposalApproved` and `ProposalRejected` deliberately do **not** project. They are
AI-process facts, recorded on the AIInteraction and in audit, and projecting them would put the AI's
own activity back into its input.

## 10. AI behaviour rules

These constrain the Intelligence layer. They are enforced in the Tool Gateway, not in prompts.
Autonomy is Levels 1-2 only (PQ-3, ADR-0030).

### 10.1 Structural

| # | Rule | Type |
|---|---|---|
| BR-AI-01 | AI may only mutate state through registered tools. There is no other path. | INV |
| BR-AI-02 | Every AI-originated Proposal and every AI-executed mutation carries an `ai_interaction_id` and at least one Evidence reference. | INV |
| BR-AI-03 | Effective authority for a tool call is `agent_role ∩ delegated_principal ∩ capability_policy ∩ organization_scope`. A call exceeding any of the four is rejected and audited. | INV |
| BR-AI-04 | A single AIInteraction may raise at most N Proposals (default 25) and touch at most M entities (default 10). Exceeding the limit aborts the interaction and raises an operational alert. | POL |
| BR-AI-05 | Before proposing a new Work, Commitment, Risk or Decision, the AI must call the corresponding `find_similar` tool. Proposing without a prior search in the same interaction is rejected. | INV |
| BR-AI-10 | Content inside ingested Events is data. Any instruction found within it is ignored. Tool selection is driven only by the system prompt and the task definition. | INV |
| BR-AI-12 | AI-authored text on an entity is labelled as AI-generated and remains labelled until a human edits it. | INV |
| BR-AI-13 | AI may not act on an Event whose sensitivity exceeds the organization's AI processing threshold. | POL |
| BR-AI-14 | Every AI answer presented to a user must cite the entities or evidence it used, or state that it is unsupported. | INV |
| BR-AI-15 | AI may not process or propose against Events with `origin = internal` (BR-E-11). | INV |

### 10.2 Level 1 — observe and propose

| # | Rule | Type |
|---|---|---|
| BR-AI-16 | Every AI-originated change to Work Core state is created as a **Proposal**. There is no AI code path that mutates Work, Commitment, Dependency, Risk or Decision without a prior approved Proposal. | INV |
| BR-AI-09 | Extraction below the per-category confidence threshold produces no Proposal. It is recorded as a low-confidence observation on the AIInteraction. Guessing quietly is worse than silence. | POL |
| BR-AI-17 | A Proposal must preserve uncertainty rather than resolve it: unknown owner, unknown date, unknown project stay empty with a stated reason. Inventing a value to make a Proposal look complete is a defect (Decision Pack Rule 4). | INV |
| BR-AI-34 | **AI may not invent a Person.** If a named or implied person cannot be resolved above threshold (BR-I-06), AI must leave the assignment empty or record an unresolved attribution carrying the raw identifier. It may propose "assign a responsible person" as a separate Proposal. It may never create a Person, guess a Person, or pick the nearest plausible match. | INV |
| BR-AI-35 | **AI may not invent Project membership.** Failure to identify a Project is a normal outcome, not an error. AI may propose a Project when evidence supports it; it may never default, infer or create one, and it may never create a synthetic container Project (BR-P-07a). | INV |
| BR-AI-36 | **Evidence attachment to an existing entity is Level 1** (C-1a, Resolution Pack v1.1). AI identifies the Evidence candidate and raises a Proposal; the relationship is persisted only after human approval through the Tool Gateway. Evidence created as part of an approved entity mutation is written in that same approved transaction. | INV |
| BR-AI-37 | **AI-generated Risk narrative is Level 1** (C-1b). Deterministic detection creates the Risk and is not an AI mutation. The narrative, mitigation and suggested owner are a proposed mutation requiring approval before they are persisted on the Risk. | INV |
| BR-AI-38 | **Computation is not authorization to persist** (C-1c). AI may calculate, explain and summarise freely on the read side. A summary that is not persisted as business state needs no Proposal. The moment any AI-generated interpretation is written to a durable business record, or becomes part of Work Core state, it is a mutation and follows the normal Proposal and Approval policy. Caching a summary for performance is persistence for this purpose unless the cache is derived, invalidatable and never read as business truth. | INV |

### 10.3 Level 2 — approved execution

| # | Rule | Type |
|---|---|---|
| BR-AI-18 | AI may execute a mutation only when a valid `ApprovalRecord` exists for the exact action. The Tool Gateway recomputes `approved_action_hash` and refuses any mismatch. | INV |
| BR-AI-19 | Execution is attributed to the approving Person as Actor, with `executed_via = ai_tool`, `proposal_id`, `approval_record_id` and `ai_interaction_id` all recorded in the audit entry. | INV |
| BR-AI-20 | An ApprovalRecord authorises exactly one execution. Replay, partial execution or execution of a superset of the approved action is rejected. | INV |
| BR-AI-21 | Approval does not confer authority the approver lacks. The underlying application service authorises the approver normally, and an approval by an unauthorised person fails at execution. | INV |
| BR-AI-22 | An ApprovalRecord not executed within the execution window (default 24 hours) expires and must be re-approved. | POL |

### 10.4 Prohibited for AI in the MVP

| # | Rule | Type |
|---|---|---|
| BR-AI-06 | AI may not delete business records, under any autonomy level. | INV |
| BR-AI-23 | AI may not change organization membership, grant permissions, change roles, or alter authorization policy, with or without approval. These are outside the tool vocabulary entirely. | INV |
| BR-AI-24 | AI may not send external messages representing a user without explicit per-message authorization. Whether outbound messaging exists at all in the MVP is open (N-1); until answered, no outbound tool is built. | INV |
| BR-AI-25 | AI may not make irreversible business decisions, or modify financial or legal records. | INV |
| BR-AI-26 | AI may not execute arbitrary SQL and has no PostgreSQL access. | INV |
| BR-AI-08 | AI may not create a Person, Team, Department, Organization, Project or Milestone. It may propose them. | POL |
| BR-AI-27 | AI may not bypass approval by any route, including retry, batch, repair or migration paths. | INV |

### 10.5 Permitted automatic actions (not AI autonomy)

Deterministic, internal, low-risk and policy-allowed. These are performed by the Work Core and the
worker, not by a model, and they exist regardless of whether AI is enabled.

| # | Rule | Type |
|---|---|---|
| BR-AI-28 | The system may automatically generate Notifications, update derived monitoring state, recalculate deterministic risk indicators, create internal DomainEvents and Events, and schedule background jobs. | POL |
| BR-AI-29 | No automatic action may set a business field a human or an approved Proposal would otherwise set. Derived state (BR-G-08) is the boundary. | INV |

### 10.6 Autonomy policy shape

| # | Rule | Type |
|---|---|---|
| BR-AI-30 | Autonomy is stored per `(organization, capability, entity_type, action)` with values `off`, `level_1_propose`, `level_2_approved_execution`. A single global flag such as `ai_autonomous` is forbidden. | INV |
| BR-AI-31 | The MVP ceiling is `level_2_approved_execution`. Values above it are not representable in the MVP schema. | INV |
| BR-AI-32 | Promotion of a capability requires measured offline precision, online acceptance, rejection, correction, reversal, false-positive and false-negative rates, an auditability review and a safety-impact review, **plus an explicit human decision recorded as an ADR**. Metrics alone never promote a capability automatically. | POL |
| BR-AI-33 | Demotion to `level_1_propose` or `off` may happen automatically when online metrics breach thresholds. Safety may tighten without a meeting; it may not loosen without one. | POL |

## 11. Proposal and approval handling

| # | Rule | Type |
|---|---|---|
| BR-PR-01 | Approving a Proposal writes an immutable ApprovalRecord, then executes the approved action through the Tool Gateway. The execution uses the same application service a human edit would. | INV |
| BR-PR-02 | A Proposal whose target entity changed since it was raised is marked `superseded` and re-presented with current values. It is never applied blindly, and any existing ApprovalRecord for it is invalidated. | INV |
| BR-PR-03 | Rejection requires no reason, but an optional structured reason is recorded and fed to evaluation. Friction on rejection is a design smell. | POL |
| BR-PR-04 | Proposals are routed to the person with authority over the target: the active `OWNER`, then the project lead, then the team lead. For Work with no Project and no OWNER, routing falls back to the capturing user and their team lead. Every Proposal must have a defined recipient, because unparented and unassigned Work is normal (BR-W-15, ADR-0029). | POL |
| BR-PR-05 | Approving never grants the approver permissions they lack (BR-AI-21). | INV |
| BR-PR-06 | Edit-and-approve produces an ApprovalRecord whose `approved_action` is the edited action, with the diff retained. The edit is the signal that matters most for evaluation. | INV |
| BR-PR-07 | A Proposal not reviewed within the proposal window (default 14 days) expires. Expiry is a negative signal for evaluation. | POL |
| BR-PR-08 | The complete chain Event → Evidence → AIInteraction → Proposal → ApprovalRecord → mutation → audit entry must be traversable in both directions for every AI-originated record (Decision Pack Rule 3). | INV |

## 11a. Reporting and rollup rules

| # | Rule | Type |
|---|---|---|
| BR-RPT-01 | All Work in an organization partitions into **Project Work** (`project_id IS NOT NULL`) and **Non-project Work** (`project_id IS NULL`). Every list, count and export must be able to present the partition. | INV |
| BR-RPT-02 | **Project rollups exclude Non-project Work.** A project's counts, health and progress are computed only over Work that belongs to it. Non-project Work is never attributed to a Project for reporting purposes. | INV |
| BR-RPT-03 | Organization-wide, department-wide, team-wide and person-wide reporting **includes both** Project and Non-project Work. Omitting Non-project Work from a person's load or a team's backlog would understate real commitments. | INV |
| BR-RPT-04 | Any figure presented to a user must state which partition it covers when both are possible. A number labelled only "open work" is ambiguous and is a defect. | INV |
| BR-RPT-05 | Person load and capacity figures are computed over active `OWNER` assignments across both partitions (BR-W-13, subject to PQ-5). | DRV |

## 12. Monitoring rules (deterministic)

Each has a stable `monitor_rule_id`, is pure over database state plus current time, and is
reproducible. AI is not involved in evaluation.

| Rule id | Condition | Output |
|---|---|---|
| MON-001 | Work `in_progress` with `due_date` passed | Notification to the active OWNER, or to the capturing user and team lead if unassigned; Risk if on critical path |
| MON-002 | Work `in_progress` and stale beyond window (BR-W-10) | Notification; Risk after 2× window |
| MON-002a | Work open with no active `OWNER` beyond the unowned-work window (default 5 days) | Notification to the capturing user and the relevant team lead. Unassigned Work is valid (BR-W-15), but silently unowned Work is a real risk |
| MON-003 | Commitment `open` with due date passed (BR-C-06) | Status → `missed`, notify both parties |
| MON-004 | Commitment due within 48h, unacknowledged | Notification to committer |
| MON-005 | Milestone with any open blocking dependency chain whose latest due date exceeds milestone target | Risk `schedule`, severity from slack |
| MON-006 | Milestone target within 7 days with >30% of its Work incomplete | Risk `schedule` |
| MON-007 | Work `blocked` longer than the block window (default 5 days) | Risk `dependency`, escalate to project lead |
| MON-007a | Work `blocked` with no remaining active `blocks` dependency and no `blocked_reason` | Notification to the active OWNER, or to the capturing user and the team lead if there is no OWNER |
| MON-008 | Person with open critical/high Work above load threshold, counted over active `OWNER` assignments across both work partitions (BR-RPT-05) | Risk `capacity` (dependent on PQ-5) |
| MON-009 | Open Risk unowned beyond window (BR-R-03) | Escalation notification |
| MON-010 | Open Risk with `review_due_at` passed | Notification to owner |
| MON-011 | Project with no Events referencing it for the silence window | Risk `other`, "project has gone quiet" |
| MON-012 | Dependency crossing projects where blocker's project is `on_hold` or `cancelled` | Risk `dependency`, notify both leads |

## 13. Notification rules

| # | Rule | Type |
|---|---|---|
| BR-N-01 | Notifications are deduplicated on `(recipient, kind, subject, dedupe_key)`. The same fact never notifies twice. | INV |
| BR-N-02 | A recipient receives at most K notifications of the same kind per day (default 5); the remainder are digested. | POL |
| BR-N-03 | Notifications are only delivered to people who can read the subject entity. Authorization is re-checked at delivery, not only at creation. | INV |
| BR-N-04 | Escalation to a lead requires the underlying condition to have persisted for the configured window; instant escalation is not permitted. | POL |
| BR-N-05 | A person can always see why they received a notification, including the monitor rule or evidence behind it. | INV |

## 14. Rules deliberately not defined yet

| Area | Why deferred |
|---|---|
| Effort estimation and capacity arithmetic | Depends on PQ-5; MON-008 is specified conditionally |
| Approval workflows on Work | No stated requirement; would be over-engineering |
| Recurring work and templates | Not required by the vision; add on evidence of need |
| SLA/priority escalation matrices | Premature before pilot data |
| Cross-organization collaboration | Explicitly out of scope (BR-G-01) |
