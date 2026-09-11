# Domain Model — AI WorkOS

Version 0.1 · Status: proposed

Ubiquitous language, aggregates, attributes, relationships and lifecycles. Invariants and rule
numbers live in `docs/domain/business-rules.md`; this document defines shape, that one defines
behaviour.

---

## 1. Ubiquitous language

| Term | Definition | Not to be confused with |
|---|---|---|
| **Work** | A unit of intended or tracked effort with an owner and a state | A task list item in a source system |
| **Commitment** | A promise by a Person to deliver something to someone by a time | Work. A commitment is social; work is operational |
| **Dependency** | A directed relation where one item's progress constrains another's | A parent/child relation |
| **Risk** | A condition that may prevent an outcome, with likelihood and impact | An issue, which has already happened |
| **Decision** | A recorded choice with rationale, scope and validity over time | An action item |
| **Event** | An immutable record of observed activity | A domain event (internal state-change fact) |
| **Evidence** | A link asserting that an excerpt of an Event supports a claim about an entity | A citation in prose |
| **Proposal** | A pending, reviewable change suggested by AI | An applied change |
| **AIInteraction** | One bounded unit of AI work: trigger, inputs, tool calls, outputs, cost | A chat message |
| **Finding** | A deterministic monitor's output | A Risk. A finding may create a Risk |
| **WorkAssignment** | The canonical record of a Person's role on a Work item | An assignee field |
| **OWNER** | The single accountable person for a Work item, if there is one | A manager or approver |
| **Actor** | Whoever caused a change: Person, System, or AI-on-behalf-of-Person | User |

## 2. Aggregate map

Aggregates are transaction and invariant boundaries. Everything outside an aggregate is referenced by
id, never by object graph.

| Aggregate root | Members | Referenced by id |
|---|---|---|
| Organization | settings, retention policy | — |
| Department | — | Organization, parent Department |
| Team | TeamMembership | Organization, Department |
| Person | RoleAssignment, ExternalIdentity | Organization |
| Project | Milestone | Organization, Team, Person |
| Work | WorkAssignment | Project (**optional**), Milestone, parent Work, Person |
| Dependency | — | two Work or Work↔Milestone endpoints |
| Commitment | — | Person (from/to), Work (optional), Project |
| Risk | Mitigation (v1: fields, not entity) | scope target, owner |
| Decision | — | scope target, decider, supersedes Decision |
| Event | EventParticipant | Organization, Person candidates, source |
| Evidence | — | Event, target entity, producing actor |
| Proposal | ProposedChange, ApprovalRecord | AIInteraction, target entity |
| Notification | — | recipient Person, subject entity |
| AIInteraction | ToolCall, TokenUsage | Organization, principal, Events, Proposals |

Rule: one aggregate per transaction wherever possible. Cross-aggregate consistency is achieved with
domain events via the outbox, not with distributed locks. The exception, permitted explicitly:
creating an entity plus its Evidence rows plus the audit entry happens in one transaction, because
provenance must never be separable from the fact (P3).

## 3. Entity relationship overview

```
Organization 1─────* Department ──┐ (self-parent, tree)
     │ 1                          │
     │                            │ 0..1
     ├───────* Team *─────────────┘
     │            │ *   * (TeamMembership)
     ├───────* Person ──────────────┐
     │            │                 │
     │            │ via WorkAssignmt│ made_by / made_to
     ├───────* Project              │
     │            │ 1               │
     │            ├──* Milestone    │
     │            │      ▲          │
     │            │      │ targets  │
     │            └──0..* Work ────┼──* Commitment
     │                  (Work.project_id is NULLABLE — A-7/ADR-0029)
     │                  │ ▲ parent  │     │ fulfilled_by (0..1)
     │                  │ └─self    │     ▼
     │                  │           └──► Work
     │                  │ *
     │           Dependency (blocker → blocked)
     │
     ├───────* Risk ────────► scope target (Org|Dept|Team|Project|Milestone|Work|Commitment)
     ├───────* Decision ────► scope target, supersedes Decision (0..1)
     │
     ├───────* Event 1──* Evidence *──► any Work-Core entity
     │            │
     │            └──* EventParticipant ──► Person (resolved) | raw handle
     │
     ├───────* AIInteraction 1──* ToolCall
     │              │ 1──* Proposal 1──* ProposedChange
     │              └───────* Evidence (produced_by)
     │
     └───────* Notification ──► recipient Person, subject entity
```

## 4. Identity & Organization context

### Organization
Tenant root. Every other row carries `org_id`.

| Field | Type | Notes |
|---|---|---|
| id | uuid | |
| name, slug | text | |
| timezone | text | default for date interpretation |
| fiscal_year_start | date | for rollups |
| ai_enabled | bool | master switch; false must leave the system fully functional |
| autonomy_policy | jsonb | per capability, entity type and action: `off` \| `level_1_propose` \| `level_2_approved_execution` (BR-AI-30) |
| retention_policy | jsonb | event/raw payload retention windows |
| status | enum | active, suspended |

### Department
Tree within an organization. `parent_department_id` nullable. Has a lead Person. Used for rollups and
for visibility scoping.

### Team
Delivery unit. Optionally belongs to a Department. Has a lead. Members via `TeamMembership`
(`person_id`, `team_id`, `role` in {lead, member, guest}, `from`, `to`). A Person may be in many
teams; historical membership is retained.

### Person
Local projection of a human. Not a credential store.

| Field | Type | Notes |
|---|---|---|
| id | uuid | |
| org_id | uuid | |
| keycloak_subject | text, nullable | null for people who are represented but do not log in |
| display_name, email | text | |
| primary_team_id, department_id | uuid, nullable | |
| status | enum | active, inactive, departed |
| working_hours, timezone | jsonb/text | used by monitors for due-date reasoning |

### ExternalIdentity
`(person_id, source_system, external_id, handle, confidence, confirmed_by, confirmed_at)`.
The bridge between "@dana in chat" and a Person. Unconfirmed mappings above threshold may be used for
suggestions; below threshold they may not be used for attribution (A-5).

### RoleAssignment
`(person_id, role, scope_type, scope_id)`. See `docs/security/security-model.md` §3.

## 5. Work Core context

### Project
| Field | Type | Notes |
|---|---|---|
| id, org_id | uuid | |
| name, description | text | |
| objective | text | what success means, used by AI for relevance judgements |
| owning_team_id, department_id | uuid nullable | |
| lead_person_id, sponsor_person_id | uuid nullable | |
| status | enum | proposed, active, on_hold, completed, cancelled |
| start_date, target_date, actual_end_date | date | |
| health | enum, derived | on_track, at_risk, off_track (computed by monitors, never typed) |
| visibility | enum | organization, department, team, restricted |
| source | enum | human, ai, import |

### Milestone
| Field | Notes |
|---|---|
| project_id | required |
| name, description | |
| target_date, actual_date | |
| acceptance_criteria | text, optional; gives completion a testable meaning |
| status | planned, in_progress, achieved, missed, cancelled |
| order_index | display ordering within project |

### Work
The central entity. Hierarchical, but shallow by rule (BR-W-06).

| Field | Type | Notes |
|---|---|---|
| id, org_id | uuid | |
| project_id | uuid, nullable | **Optional by decision (ADR-0029).** Work without a Project is fully functional, not a degraded state |
| milestone_id | uuid, nullable | must belong to the same project |
| parent_work_id | uuid, nullable | max depth 3 |
| title, description | text | |
| type | enum | deliverable, task, activity, investigation |
| *(no assignee column)* | — | **Assignment lives exclusively in `WorkAssignment`** (ADR-0032). There is no `assignee_person_id` and no collaborator array on Work. Fast owner lookup uses the `work_current_owner` read model, never a second mutable column |
| status | enum | see §5.1 |
| priority | enum | low, normal, high, critical |
| due_date | date, nullable | interpreted in owner's timezone |
| started_at, completed_at | timestamptz | |
| estimate | interval or points, nullable | subject to PQ-5 |
| confidence | smallint 0-100, nullable | extraction confidence at creation, not progress |
| source | enum | human, ai, import |
| origin_event_id | uuid, nullable | first Event that produced it |
| last_signal_at | timestamptz | last Event that referenced it; drives staleness monitors |
| visibility | enum | inherits Project by default |

Deliberately absent in v1: time tracking, custom fields, workflows per project, sprints, story points
as a required concept, attachments on Work (attachments belong to Events/Evidence).

#### 5.1 Work lifecycle

```
         ┌──────────┐  confirm   ┌────────┐  start    ┌─────────────┐  complete ┌──────┐
  ──────►│ proposed │───────────►│  todo  │──────────►│ in_progress │──────────►│ done │
         └────┬─────┘            └───┬────┘           └──────┬──────┘           └──┬───┘
              │ reject               │                       │ block/unblock       │ reopen
              ▼                      │                  ┌────▼────┐                │
         ┌──────────┐                │                  │ blocked │◄───────────────┘
         │ rejected │                │                  └────┬────┘
         └──────────┘                │                       │
                                     ▼                       ▼
                                ┌───────────┐  ◄───────────────
                                │ cancelled │
                                └───────────┘
```

`proposed` exists only for AI-originated work under propose mode. Human-created work starts at `todo`.
`blocked` requires a reason or a blocking Dependency (BR-W-04).

### WorkAssignment
**Canonical source of truth for Work-to-Person assignment** (Resolution Pack v1.1, ADR-0032, accepted).
A Work may carry zero, one or many assignments. Nothing else in the model records who is on a piece of
work.

| Field | Type | Notes |
|---|---|---|
| id, org_id | uuid | organization-scoped like every other entity (BR-G-01) |
| work_id | uuid | |
| person_id | uuid | must be an `active` Person in the same organization |
| role | enum | `OWNER` \| `CONTRIBUTOR` \| `REVIEWER` |
| is_primary | bool | primary point of contact within a role; at most one active `is_primary` per Work |
| status | enum | `active` \| `ended` |
| assigned_at, assigned_by_actor | timestamptz, jsonb | who made the assignment: Person, system, or AI-with-approval |
| started_at, ended_at | timestamptz, nullable | working period; ending an assignment sets `ended_at` and `status = ended` |
| source | enum | human, ai, import |
| created_at, updated_at | timestamptz | |

**Cardinality (binding).**

| Role | Active per Work |
|---|---|
| `OWNER` | zero or one |
| `CONTRIBUTOR` | zero or more |
| `REVIEWER` | zero or more |

**Work with no assignment is valid and expected.** "Someone please check the IOC API before Friday."
produces Work with a due date and no assignment, plus optionally a Proposal to assign a responsible
person. The system never invents an assignee (BR-W-15, BR-AI-34).

**Read model, not a mirror.** `work_current_owner` is a database view or projection over
`WorkAssignment` where `role = OWNER` and `status = active`. It is derived, never written, and carries
no authority of its own. Introducing a writable owner column on Work would create a second mutable
source of truth and is forbidden.

### Dependency
| Field | Notes |
|---|---|
| blocker_type, blocker_id | Work or Milestone |
| blocked_type, blocked_id | Work or Milestone |
| kind | `blocks` (hard) \| `informs` (soft) |
| status | active, resolved, withdrawn |
| cross_project | derived bool, used for escalation |
| rationale | text, optional |

Directed acyclic across `blocks` edges (BR-D-02). Cross-organization dependencies are forbidden.

## 6. Commitment & Accountability context

A Commitment is a promise, recorded whether or not anyone created a task for it. This is the entity
that most directly justifies capture from conversation: promises are made verbally and then
evaporate.

| Field | Type | Notes |
|---|---|---|
| id, org_id | uuid | |
| statement | text | the promise in the committer's own words where possible |
| committed_by_person_id | uuid | required |
| committed_to_person_id | uuid, nullable | null if made to a team or the room |
| committed_to_team_id | uuid, nullable | |
| due_date, due_precision | date, enum (exact, week, month, vague) | conversational dates are rarely exact |
| status | enum | see §6.1 |
| fulfilling_work_id | uuid, nullable | link to Work if one exists |
| project_id | uuid, nullable | |
| origin_event_id | uuid | the Event where the promise was made |
| confidence | smallint | extraction confidence |
| acknowledged_at | timestamptz, nullable | committer confirmed it exists |

### 6.1 Commitment lifecycle

```
  captured ──acknowledge──► open ──┬── fulfilled
     │                             ├── renegotiated ──► open (new due_date, prior retained)
     ├── disputed                  ├── missed  (due date passed, not fulfilled)
     └── withdrawn                 └── cancelled
```

`captured` means the AI heard it and nobody has confirmed. `disputed` is a first-class outcome: the
named committer says they did not promise that. Disputed commitments are retained with their evidence
because disputes are themselves signal (BR-C-05).

## 7. Governance context

### Risk
| Field | Notes |
|---|---|
| title, description | |
| category | schedule, capacity, dependency, scope, quality, external, people, other |
| scope_type, scope_id | Organization \| Department \| Team \| Project \| Milestone \| Work \| Commitment |
| likelihood, impact | 1–5 each |
| severity | derived = likelihood × impact, banded low/medium/high/critical |
| status | open, mitigating, accepted, closed, realized |
| owner_person_id | nullable but required to leave `open` (BR-R-03) |
| mitigation | text |
| detected_by | human \| monitor \| ai |
| monitor_rule_id | nullable; set when a deterministic finding created it |
| first_detected_at, last_evaluated_at, review_due_at | |

`realized` matters: it closes the loop for evaluating whether detection was useful.

### Decision
| Field | Notes |
|---|---|
| title | |
| context | the situation requiring a decision |
| statement | what was decided |
| rationale | why |
| alternatives_considered | text or jsonb list |
| scope_type, scope_id | as Risk |
| status | proposed, accepted, rejected, superseded |
| decided_by_person_id, decided_at | |
| supersedes_decision_id | nullable |
| reversibility | easy, costly, irreversible (informs how hard the system pushes for rationale) |

Decisions are never edited after `accepted`; they are superseded (BR-DE-02). This is what makes
"why did we do this?" answerable six months later.

## 8. Signal context

### Event
| Field | Type | Notes |
|---|---|---|
| id, org_id | uuid | |
| source_system | text | registered source key: `web`, `openclaw.whatsapp`, `workos.internal` |
| source_ref | text | external id; unique with source_system |
| content_hash | text | dedup |
| origin | enum | `external` (captured from outside) \| `internal` (produced by the Work Core). Only `external` is queued for AI extraction (BR-E-11) |
| origin_domain_event_id | uuid, nullable | set when the Event was projected from a DomainEvent |
| type | enum | `EXTERNAL_MESSAGE`, `MANUAL_CAPTURE`, `MEETING_NOTE`, `COMMENT`, `ATTACHMENT`, `SYSTEM_ACTIVITY` (Resolution Pack v1.1). The enum may be extended later; these six are the binding minimum |
| channel | text, nullable | `web`, `whatsapp`, future channels; null for internal |
| sender_external_id | text, nullable | e.g. WhatsApp phone number, before Person resolution |
| occurred_at, observed_at | timestamptz | when it happened vs when we learned |
| title | text, nullable | |
| body_text | text, nullable | normalised text used for extraction and excerpts |
| raw_payload_uri | text | MinIO object |
| sensitivity | enum | normal, confidential, restricted |
| participant_count | int | |
| processing_status | enum | received, normalised, extracted, failed, skipped |
| retention_expires_at | timestamptz | from org policy |

`body_text` is stored in Postgres so evidence excerpts remain readable even if blob storage is
unavailable or the raw payload has been purged for retention.

**Comments are Events** (N-3, ADR-0033). There is no `Comment` entity in the MVP. A comment on a Work
item is an Event with `type = COMMENT`, targeted at the entity through Evidence or a subject reference,
and `origin = INTERNAL`. Threaded discussion, mentions, reactions, subscriptions and moderation are
explicitly out of MVP scope; a Comment aggregate is introduced only when one of those is required.

One consequence needs a decision and is **not** resolved here: under BR-E-11 internal-origin Events are
never extracted, so a comment reading "I'll finish this by Friday" would not produce a Commitment
Proposal. The pack says `origin = INTERNAL` "where appropriate", which leaves room. Tracked as **N-4**.

### EventParticipant
`(event_id, person_id nullable, external_handle, role: organiser|speaker|mentioned|recipient,
match_confidence)`. Mentions matter: a commitment is often made *about* someone not present.

### Evidence
| Field | Type | Notes |
|---|---|---|
| id, org_id | uuid | |
| event_id | uuid | |
| locator | jsonb | `{char_start, char_end}` or `{ts_start, ts_end}` or `{message_id}` |
| excerpt | text | the literal quoted span, stored denormalised on purpose |
| target_type, target_id | polymorphic | Work, Commitment, Risk, Decision, Project, Milestone, Dependency |
| assertion | enum | creates, supports, completes, updates, reassigns, reschedules, contradicts, closes |
| confidence | smallint 0-100 | |
| produced_by_type | enum | ai_interaction, person |
| produced_by_id | uuid | |
| superseded_by_id | uuid, nullable | |
| created_at | timestamptz | |

Evidence is the join between the world and the model. If a query cannot be answered with evidence,
the product should say so rather than assert.

### EventAttachment
`(event_id, object_uri, filename, media_type, size_bytes, uploaded_by_person_id, checksum)`. The web
capture surface allows attaching supporting files to an Event. Attachments can be Evidence targets via
a locator of `{attachment_id}`; excerpt verification does not apply to binary attachments, and Evidence
over an attachment records `excerpt = null` with a human or AI-authored `claim_summary` instead.

### DomainEvent (technical, not a domain entity)
`(id, org_id, occurred_at, type, aggregate_type, aggregate_id, payload, actor, causation_id,
correlation_id)`, written to the outbox in the same transaction as the state change.

Types in v1: `WorkCreated`, `WorkUpdated`, `WorkAssigned`, `WorkStatusChanged`, `WorkCompleted`,
`CommitmentCreated`, `CommitmentAcknowledged`, `CommitmentCompleted`, `CommitmentMissed`,
`DependencyLinked`, `RiskRaised`, `RiskStatusChanged`, `DecisionCreated`, `DecisionAccepted`,
`ProposalRaised`, `ProposalApproved`, `ProposalRejected`, `NotificationSent`.

A subset of these projects into internal Events (business rules §9a). The projection is one-directional
and allow-listed: a DomainEvent may create an Event, and an Event never creates a DomainEvent except
through an application service acting on a human or approved-AI instruction.

## 9. Intelligence context

### AIInteraction
| Field | Notes |
|---|---|
| id, org_id | |
| kind | extraction, linking, monitoring_explanation, summarisation, question_answer, evaluation |
| trigger_type | event, schedule, user_request, retry |
| trigger_ref | id of the triggering thing |
| principal_person_id | the human whose authority is delegated, nullable for system-scope agents |
| agent_identity | service account used |
| runtime, model, model_version | OpenClaw runtime + model routing metadata |
| prompt_id, prompt_version | pinned, never "latest" |
| tool_manifest_version | |
| input_refs | jsonb: event ids, entity ids provided as context |
| status | running, succeeded, failed, rejected, timed_out |
| started_at, finished_at, latency_ms | |
| token_usage, cost_estimate | |
| output_summary | jsonb: counts of proposals/entities/evidence produced |
| error | nullable |

### ToolCall
`(ai_interaction_id, sequence, tool_name, tool_version, arguments_redacted, authorization_result,
outcome, target_entity_type, target_entity_id, duration_ms, error)`. Every call, including rejected
ones. Rejections are the most interesting rows in the table.

### Proposal / ProposedChange
The reviewable form of an AI write when autonomy policy is `propose`.

- **Proposal**: `(ai_interaction_id, org_id, kind: create|update|link|close, target_type,
  target_id nullable, summary, confidence, status: pending|accepted|accepted_with_edits|rejected|
  expired|superseded, reviewed_by, reviewed_at, rejection_reason)`
- **ProposedChange**: `(proposal_id, field_path, current_value, proposed_value)`

### ApprovalRecord
Required by PQ-3 Level 2. An immutable record that binds a human's authority to an AI-executed
mutation. It is separate from `Proposal.status` because status is mutable and an approval must not be.

| Field | Type | Notes |
|---|---|---|
| id, org_id | uuid | |
| proposal_id | uuid | |
| approver_person_id | uuid | the human whose authority is delegated for execution |
| decision | enum | `approved`, `approved_with_edits`, `rejected` |
| approved_action | jsonb | **the exact action as approved**, including the tool name, tool version and fully resolved arguments. Frozen at approval time |
| approved_action_hash | text | the Tool Gateway refuses to execute anything whose action hash does not match |
| edits | jsonb, nullable | diff between the proposed and the approved action |
| decided_at | timestamptz | |
| execution_status | enum | pending, executed, failed, expired |
| resulting_entity_type, resulting_entity_id | polymorphic, nullable | filled on successful execution |
| resulting_audit_entry_id | uuid, nullable | the audit row for the mutation |
| executed_at, execution_error | | |

Everything except the execution outcome fields is immutable. The outcome fields are written once.

Approving a Proposal creates the ApprovalRecord, then executes the approved action through the Tool
Gateway with the approver as Actor, `executed_via = ai_tool`, and provenance retained to the
AIInteraction. Rejections feed evaluation.

> **Note on scope.** Proposal, ProposedChange and ApprovalRecord are not in the original core entity
> list. Proposal and ProposedChange are required by PQ-3 Level 1; ApprovalRecord is required by PQ-3
> Level 2's demand that an approval be associated with the approving user, the proposal, the exact
> proposed action, the timestamp and the resulting mutation. Tracked in ADR-0011 and ADR-0030.

## 10. Notification context

| Field | Notes |
|---|---|
| recipient_person_id | |
| kind | proposal_pending, commitment_due, commitment_missed, work_blocked, risk_raised, risk_escalated, milestone_at_risk, mention, digest |
| priority | low, normal, high |
| subject_type, subject_id | |
| dedupe_key | prevents the same fact notifying twice (BR-N-01) |
| digest_group | for batching |
| state | pending, sent, read, dismissed, snoozed_until |
| channel | in_app (v1), email (v1 optional) |

## 11. Cross-cutting: Actor and Audit

Every mutation records an `Actor`:

```
Actor = { type: person | system | ai,
          person_id?,          # for person, or the delegated principal for ai
          ai_interaction_id?,  # required when type = ai
          service_account? }
```

`AuditEntry`: `(org_id, occurred_at, actor, action, entity_type, entity_id, before, after,
request_id, ai_interaction_id, authorization_context)`. Append-only, no update or delete path, retained
independently of the entities it describes.

## 12. Derived and computed state

Never stored as user-editable fields:

| Derived value | Source |
|---|---|
| Project/Milestone health | monitor rules over child work, dependencies, dates |
| Risk severity | likelihood × impact |
| Work staleness | `now - last_signal_at` |
| Commitment overdue | due_date vs now vs status |
| Person load | open Work where the Person holds an active `OWNER` assignment, weighted by priority (subject to PQ-5) |
| `work_current_owner` | view over `WorkAssignment` where `role = OWNER` and `status = active` |
| Project Work / Non-project Work split | `work.project_id IS NOT NULL` vs `IS NULL` (BR-RPT-01) |
| Dependency chain depth / critical path | graph query over active `blocks` edges |

## 13. Open modelling questions

| # | Question | Options |
|---|---|---|
| M-1 | ~~Multiple assignees on Work?~~ | **RESOLVED** (Resolution Pack v1.1, ADR-0032 accepted). WorkAssignment is canonical; many assignments; OWNER at most one active; no mirror column |
| M-2 | Should Milestone be able to depend on Work across projects? | (a) yes, polymorphic dependency endpoints (proposed); (b) Work-to-Work only |
| M-3 | Do Risks need explicit Mitigation entities with owners and dates? | (a) text field in v1 (proposed); (b) entity from the start |
| M-4 | Is `estimate` needed for useful overload detection? | see PQ-5 |
| M-5 | Should Evidence support many-to-many across multiple events for one claim? | (a) one Event per Evidence row, multiple rows per claim (proposed); (b) composite evidence object |
| M-6 | Do we need a Goal/OKR layer above Project? | Deferred. Not required by the stated vision; revisit only on demand |
| M-7 | ~~Is there a `Comment` entity?~~ | **RESOLVED** (N-3, ADR-0033). No Comment entity. `Event.type = COMMENT` |
| N-4 | Are `COMMENT` Events eligible for AI extraction, given that they are human-authored but internal-origin? | (a) no, internal origin means no extraction, matching BR-E-11 literally (**current behaviour**); (b) treat human-authored comments as extraction-eligible by adding `origin = INTERNAL_HUMAN`; (c) keep origin INTERNAL but add an explicit `extractable` flag set by the capture surface. Affects whether in-app conversation is a capture surface at all |
| M-8 | Do commitments captured from WhatsApp need a `channel_thread_id` for conversational continuity? | (a) no, Event linkage is enough (proposed); (b) yes, to follow a promise across a thread |
| M-9 | Does `Person` need a phone-number identity type distinct from handles, for WhatsApp resolution (PQ-7)? | (a) `ExternalIdentity` with `source_system = openclaw.whatsapp` covers it (proposed); (b) a typed contact-point model |
