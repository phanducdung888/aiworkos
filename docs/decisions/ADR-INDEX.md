# Architecture Decision Records — Index

Version 0.3 · Records 0001–0025 are **proposed** and take effect on Phase 0 sign-off.
Records 0026–0031 derive from **Decision Pack v1.0** and are accepted. ADR-0032 is accepted with
amended semantics, and 0033–0034 are added, by **Resolution Pack v1.1**.
See `docs/decisions/decision-pack-v1.0.md` and `docs/decisions/resolution-pack-v1.1.md`.

Each entry summarises context, decision and consequences. When a record becomes contested or needs
fuller treatment, promote it to its own file `docs/decisions/ADR-nnnn-slug.md` using the template in
§3. Superseded records are never edited; a new record supersedes them.

---

## 1. Index

| # | Title | Status | Relates to |
|---|---|---|---|
| 0001 | Modular monolith with enforced context boundaries | proposed | arch §4 |
| 0002 | AI has no database access; Tool Gateway is the sole mutation path | proposed | ai §1 |
| 0003 | PostgreSQL is the only system of record | proposed | arch §3 |
| 0004 | Separate Event, Evidence and DomainEvent concepts | **accepted** (Decision Pack, A-1) | arch §6 |
| 0005 | Transactional outbox for domain events | proposed | arch §6 |
| 0006 | Redis Streams as the work queue | proposed | arch §6, A-8 |
| 0007 | Keycloak as identity provider with a local Person projection | proposed, amended by 0031 | sec §2 |
| 0008 | Single database, `org_id` everywhere, RLS as defence in depth | **accepted** (Decision Pack, PQ-2) | sec §3.4 |
| 0009 | RBAC with resource scoping, evaluated in one policy module | proposed | sec §3 |
| 0010 | AI authority is the intersection of agent role and delegated principal | proposed | sec §4.2 |
| 0011 | Proposal as a first-class entity; autonomy is policy | proposed, **amended by 0030** | ai §6 |
| 0012 | Deterministic monitors detect; AI interprets | proposed | ai §2 |
| 0013 | Evidence is mandatory for AI-originated writes | proposed | domain §8 |
| 0014 | Raw payloads in MinIO, normalised text in Postgres | proposed | domain §8 |
| 0015 | REST + OpenAPI 3.1; no GraphQL in v1 | proposed | arch §7 |
| 0016 | React + TypeScript + Vite with server-state-only data flow | proposed | arch §8 |
| 0017 | pgvector for entity linking and semantic search | proposed | ai §4.1, AI-3 |
| 0018 | Ingestion idempotency on (org, source_system, source_ref) | proposed | rules BR-E-02 |
| 0019 | Prompts and tool manifests are versioned artefacts gated by evals | proposed | ai §7, §8 |
| 0020 | Docker Compose for dev and pilot; network segmentation as a control | proposed | arch §9 |
| 0021 | SQLAlchemy 2.0 + Alembic; no repository abstraction over the ORM | proposed | CLAUDE §6 |
| 0022 | No hard deletes where audit or evidence exists | proposed | rules BR-G-04 |
| 0023 | Shallow Work hierarchy and acyclic dependency graph | proposed | rules BR-W-06, BR-D-02 |
| 0024 | UTC storage with explicit timezone interpretation for human dates | proposed | rules BR-G-05 |
| 0025 | `AgentRuntime` port isolates OpenClaw | proposed, extended by 0027 | arch A-3 |
| 0026 | MVP capture surface: web paste, OpenClaw/WhatsApp, internal Events | accepted | PQ-1 |
| 0027 | OpenClaw holds two roles with two principals: ChannelAdapter and AgentRuntime | accepted | PQ-1, A-3 |
| 0028 | Internal activity projects into Events, with extraction loop prevention | accepted | PQ-1, A-1 |
| 0029 | Project is an optional relationship on Work | accepted, extended by Resolution Pack v1.1 | A-7 |
| 0030 | Autonomy Levels 1–2 with immutable ApprovalRecords | accepted | PQ-3 |
| 0031 | Single shared Keycloak realm; org membership resolved in the application | accepted | PQ-2 |
| 0032 | `WorkAssignment` is the canonical assignment source of truth | **accepted** (Resolution Pack v1.1) | M-1 |
| 0033 | Comments are Events, not a Comment aggregate | accepted | N-3 |
| 0034 | Persistence, not computation, is what requires autonomy policy | accepted | C-1 |
| 0035 | Cross-context references are validated through the published interface, never by the foreign key | accepted | W-11 |
| 0036 | People are provisioned administratively; there is no just-in-time creation | accepted | W-13 |
| 0037 | `ExternalIdentity` is its own authorization resource, and confirmation is not self-service | accepted | W-12, PQ-7 |
| 0038 | Event immutability is a column-scoped database trigger, not a convention | accepted | domain §Event, BR-E-01 |
| 0039 | Attachment bytes never pass through the API; an ObjectStore port fronts MinIO | accepted | arch §storage, BR-E-14 |

---

## 2. Records

### ADR-0001 — Modular monolith with enforced context boundaries
**Context.** Seven bounded contexts, a small team, Compose deployment, pilot-scale load. Microservices
would buy independent scaling we do not need and cost us distributed transactions, cross-service
authorization and deployment complexity we cannot afford.
**Decision.** One deployable application, one database, seven context packages with published
interface modules. Cross-context access only through those interfaces, enforced by import-linter in
CI. `api` and `worker` are entry points into the same codebase.
**Consequences.** Simple transactions and local calls. Boundaries must be defended by tooling, since
nothing physical prevents a shortcut. Extraction of a context later stays feasible because the
interfaces already exist. Rejected: microservices; a single unstructured application.

### ADR-0002 — AI has no database access; Tool Gateway is the sole mutation path
**Context.** The hard product rule. A rule enforced only by convention will eventually be broken by a
convenient shortcut under delivery pressure.
**Decision.** Three independent enforcement layers: network topology excludes the agent from the data
network; no data-tier credentials exist in the agent image; CI import-boundary tests forbid the code
path. All AI mutation flows through a Tool Gateway that authenticates, authorises, validates, limits
blast radius, requires evidence and audits.
**Consequences.** Every AI capability requires a purpose-built tool, which slows feature work and is
the point. Two layers can fail before the invariant does. The gateway is a bottleneck by design and
must be kept fast and well-tested.

### ADR-0003 — PostgreSQL is the only system of record
**Context.** Candidate stores: Postgres, a document store for events, a graph database for
dependencies, a search cluster, a vector database.
**Decision.** Postgres holds all state, including `jsonb` payload metadata, recursive dependency
queries and (per ADR-0017) vectors. Redis is a cache and queue and may be lost. MinIO holds raw blobs
only.
**Consequences.** One backup story, one transaction boundary, one operational skill. Graph traversal
by recursive CTE is adequate at pilot depth and will need review if dependency chains grow long.
Rejected: a graph database, on the grounds that our graphs are small and our consistency needs are not.

### ADR-0004 — Separate Event, Evidence and DomainEvent concepts
**Context.** "Event" names three different things: observed activity, an assertion linking activity to
a claim, and an internal state-change fact. Conflating them produces a model where nobody can say what
is immutable.
**Decision.** `Event` is observed activity (immutable). `Evidence` links an excerpt to a claim
(immutable, supersedable). `DomainEvent` is internal and lives in the outbox. Ambiguity A-1 offers
renaming `Event` to `ActivitySignal`; this record keeps `Event` because it matches the given entity
list, and requires that internal facts are always called domain events in code and conversation.
**Consequences.** Clear immutability rules per concept. Ongoing naming discipline required. Revisit
before the schema is written if the team keeps tripping over it.

### ADR-0005 — Transactional outbox for domain events
**Context.** State changes must reliably trigger notifications, monitors and projections without
two-phase commit across Postgres and Redis.
**Decision.** Domain events are written to an `outbox` table in the same transaction as the state
change, then dispatched by the worker with at-least-once delivery. Consumers are idempotent.
**Consequences.** No lost triggers across restarts; recovery is re-dispatch. Dispatch latency is bound
by the polling interval. This is not event sourcing: the outbox is a delivery mechanism, and the
current-state tables remain authoritative.

### ADR-0006 — Redis Streams as the work queue
**Context.** Needed: job distribution for ingestion, extraction and monitors. Redis is already in the
stack. Alternatives: Postgres-backed queue, RabbitMQ, NATS, Celery with a broker.
**Decision.** Redis Streams with consumer groups. Redis is explicitly not the durability boundary; the
outbox is. Losing Redis costs in-flight jobs, not state.
**Consequences.** No new infrastructure. Requires re-dispatch tests (testing §3 L4). Revisit if volume
or delivery guarantees grow; the outbox makes replacement low-risk.

### ADR-0007 — Keycloak as identity provider with a local Person projection
**Amended by ADR-0031** on realm strategy.

**Context.** Keycloak is mandated. Resource-scoped permissions change frequently and must be queryable
and joinable.
**Decision.** Keycloak owns authentication and coarse platform roles. `Person` and `RoleAssignment`
are local and authoritative for domain authorization. A Person may exist without a Keycloak subject so
that people can be referenced before or without having accounts.
**Consequences.** Tokens stay small and stable. A provisioning/sync path between Keycloak and Person
is required and must handle deactivation. Rejected: putting fine-grained permissions in token claims.

### ADR-0008 — Single database, `org_id` everywhere, RLS as defence in depth
**Context.** Tenancy model undecided (PQ-2). Retrofitting isolation is expensive; over-building it is
waste.
**Decision.** One database, `org_id` on every domain table, application-level scoping in every query,
plus RLS policies keyed on a per-transaction session variable. Adopted even if the pilot is single-org.
**Consequences.** Safe default under either PQ-2 answer. Small per-query overhead. RLS must be tested
explicitly (L7) or it will silently rot. Rejected: schema-per-tenant and database-per-tenant, both
premature.

### ADR-0009 — RBAC with resource scoping, evaluated in one policy module
**Context.** Roles map to organizational structure; reach is determined by department, team, project
and visibility. A general policy engine (OPA, Casbin) would add a language and a deployment.
**Decision.** Seven roles with scope, evaluated by `platform/authz` and called from application
services only. Decisions are explainable and logged. The authorization matrix is a data file, tested
exhaustively.
**Consequences.** One place to reason about access; one place for a bug to hide, which is why the
matrix test is exhaustive. If per-customer custom policies ever appear, revisit.

### ADR-0010 — AI authority is the intersection of agent role and delegated principal
**Context.** An agent with its own broad permissions would become a privilege-escalation path
reachable by anyone who can get text into an ingested Event.
**Decision.** Every tool call carries a delegation assertion. Effective permission is the intersection
of the agent identity's role and the delegated principal's permissions. There is no AI-admin path.
**Consequences.** Injection cannot exceed the principal's reach. Background extraction needs a
deliberately weak system principal with propose-only write scope. Delegation assertions are
short-lived and bound to one interaction.

### ADR-0011 — Proposal as a first-class entity; autonomy is policy
**Amended by ADR-0030.** The original text below proposed `auto_apply` defaults for several operations
and automatic promotion on metrics. Decision Pack v1.0 supersedes both. Retained for the reasoning that
led to Proposal existing at all.

**Context.** "Minimise manual entry" and "the organization owns the record" pull in opposite
directions. `Proposal` is not in the given entity list.
**Decision.** Introduce Proposal/ProposedChange. Autonomy is configured per capability and entity type
(`off` / `propose` / `auto_apply`), defaulting to `propose` for creation. Promotion to `auto_apply`
requires offline precision and online acceptance and reversal thresholds; breach demotes automatically.
**Consequences.** Trust becomes a measured property rather than a setting. Extra UI surface (the review
inbox), which is also the product's main labelling instrument.
**Amendment (ADR-0030):** autonomy values are `off`, `level_1_propose`, `level_2_approved_execution`;
no `auto_apply` for Work Core mutations; promotion always requires an explicit human decision.

### ADR-0012 — Deterministic monitors detect; AI interprets
**Context.** Risk detection could be model-driven. Model-driven detection is unreproducible, expensive
per evaluation, hard to explain to an executive and impossible to regression-test cheaply.
**Decision.** Named monitor rules (MON-001…) are pure functions of database state and time. AI is
invoked only after a finding exists, to explain and suggest.
**Consequences.** Detection is testable, cheap and defensible. Some subtle risks, the ones only visible
in language, will be missed by monitors; extraction may surface them as proposed Risks, which still
require human acceptance (BR-R-08).

### ADR-0013 — Evidence is mandatory for AI-originated writes
**Context.** The product's credibility rests on traceability.
**Decision.** Mutation tools require at least one Evidence reference; excerpts are verified verbatim
against the Event's stored text at the gateway. Entity and evidence are written in one transaction.
**Consequences.** Fabricated evidence fails server-side. Slightly heavier writes. Requires normalised
`body_text` to be retained even when raw payloads are purged (ADR-0014, BR-E-07).

### ADR-0014 — Raw payloads in MinIO, normalised text in Postgres
**Context.** Raw payloads can be large and varied (audio, video, JSON exports). Evidence needs stable,
queryable text.
**Decision.** Raw payload to MinIO with a URI on the Event; normalised `body_text` in Postgres; the
agent reads raw payloads only through short-lived presigned URLs issued after an authorization check.
**Consequences.** Postgres stays small enough to back up conventionally. Evidence survives payload
purge. MinIO outage degrades raw viewing, not the work record.

### ADR-0015 — REST + OpenAPI 3.1; no GraphQL in v1
**Context.** One first-party client, generated typing desirable, contract testing desirable.
**Decision.** Resource REST with FastAPI-generated OpenAPI, cursor pagination, `problem+json`, ETags,
idempotency keys. The client is generated and drift fails CI.
**Consequences.** Some over-fetching on dashboard screens, addressed with purpose-built `/insights`
endpoints rather than a query language. GraphQL's authorization and cost-control complexity avoided.

### ADR-0016 — React + TypeScript + Vite with server-state-only data flow
**Context.** Mandated stack. Risk is duplicating server-owned state in a client store and drifting.
**Decision.** TanStack Query owns all server state; no Redux-style global store for domain data; local
state stays local. AI attribution and evidence access are component-level requirements, tested.
**Consequences.** Fewer sync bugs, simpler mental model. Offline support is not attempted in v1.

### ADR-0017 — pgvector for entity linking and semantic search
**Context.** Duplicate creation is the fastest way to degrade the work graph (A-6). Lexical matching
alone will miss "the auth migration" versus "moving login to the new IdP".
**Decision.** Hybrid candidate retrieval: trigram/full-text plus pgvector embeddings, inside Postgres,
exposed to the AI only through `find_similar_*` tools. Mandatory search before creation (BR-AI-05).
**Consequences.** One extension, no new service. Embedding generation is an extra cost per entity and
per query. AI-3 keeps open the option of shipping lexical-only first and adding vectors once duplicate
rate is measured.

### ADR-0018 — Ingestion idempotency on (org, source_system, source_ref)
**Context.** Connectors retry, replay and backfill. Duplicate events would multiply proposals and
corrupt evidence counts.
**Decision.** Unique constraint on the triple; repeats return the existing Event; a differing
`content_hash` creates a revision Event referencing the original.
**Consequences.** Connectors can be simple and at-least-once. Source systems must expose a stable id;
where they do not, the connector must synthesise one deterministically, which needs care.

### ADR-0019 — Prompts and tool manifests are versioned artefacts gated by evals
**Context.** Prompt and model changes alter product behaviour as much as code changes, but are easy to
treat as configuration.
**Decision.** Prompts are versioned files reviewed like code; manifests are versioned; every
AIInteraction pins both plus the model version. Changes must pass the eval gates in CI, and model
upgrades are treated as releases.
**Consequences.** Regressions are attributable to a specific triple. Slower iteration on prompts,
which is the correct trade once real organizational data depends on the output.

### ADR-0020 — Docker Compose for dev and pilot; network segmentation as a control
**Context.** Compose is mandated for initial deployment. Kubernetes would add operational load a pilot
does not justify.
**Decision.** Compose with five networks (`edge`, `app`, `data`, `agent`, `egress`). The agent is
absent from `data`, which is what makes ADR-0002 structural. Only the proxy publishes ports.
**Consequences.** Single-host limits, no rolling deploys, manual failover. The network layout is a
security control and must be asserted in tests, not just documented. Migration to Kubernetes is
described but not built.

### ADR-0021 — SQLAlchemy 2.0 + Alembic; no repository abstraction over the ORM
**Context.** A repository layer per aggregate can become an anaemic passthrough over the ORM.
**Decision.** SQLAlchemy 2.0 models and typed queries, Alembic migrations, thin repository modules per
aggregate that hold queries and org scoping. No attempt at persistence-ignorance in the domain beyond
keeping domain logic free of I/O.
**Consequences.** Less ceremony; the database remains visible where it matters. Domain purity depends
on discipline and on L1 tests refusing to import session objects.

### ADR-0022 — No hard deletes where audit or evidence exists
**Context.** Provenance and audit are the trust story; deletion destroys them.
**Decision.** Lifecycle states replace deletion. Retention purges raw payloads while retaining
excerpts and audit. Person data is anonymised, not removed.
**Consequences.** Tables grow; queries filter by state; the UI must present "cancelled" clearly so it
does not look like clutter. Right-to-erasure needs a legal-reviewed anonymisation procedure (S-5).

### ADR-0023 — Shallow Work hierarchy and acyclic dependency graph
**Context.** Deep hierarchies and cyclic dependencies make rollups, health derivation and critical-path
queries expensive and ambiguous.
**Decision.** Maximum depth 3 for Work; `blocks` dependencies must remain acyclic, with the offending
path returned on rejection.
**Consequences.** Recursive queries stay cheap and comprehensible. Teams used to deeper breakdowns will
push back; the constraint is revisitable with evidence, and the cycle rule is not.

### ADR-0024 — UTC storage with explicit timezone interpretation for human dates
**Context.** Commitments extracted from conversation are full of "end of next week" across timezones.
Getting this wrong produces false "missed" states, which destroys trust in the accountability feature.
**Decision.** All timestamps UTC `timestamptz`. Human-meaningful dates are `date` plus a `due_precision`
enum, interpreted in the owner's, then the project's, then the organization's timezone. Monitors
evaluate against the interpreting timezone, not server time.
**Consequences.** More care in monitors and tests (frozen clocks, DST cases). Vague commitments are
never auto-missed (BR-C-06), which is the correct default.

### ADR-0025 — `AgentRuntime` port isolates OpenClaw
**Context.** OpenClaw is mandated but its tool protocol, sandboxing, concurrency and model routing are
not specified in our inputs (A-3). Committing the codebase to its specifics is a large unhedged bet.
**Decision.** Define an internal `AgentRuntime` port: start interaction, provide tool manifest, execute
capability, return interaction outcome. OpenClaw is one adapter; a deterministic fake is another, used
in development and in all pipeline tests.
**Consequences.** The pipeline can be built and tested before the OpenClaw spike completes, and the
runtime is replaceable. A small adapter layer to maintain, and some OpenClaw-native features may go
unused.

---

### ADR-0026 — MVP capture surface: web paste, OpenClaw/WhatsApp, internal Events
**Status:** accepted (Decision Pack v1.0, PQ-1).
**Context.** The product's value depends on capture, but connector breadth is a trap: it consumes the
whole budget before the loop is proven, and each integration carries its own auth, rate-limit and
mapping problems.
**Decision.** Three sources only. Web manual entry and paste is the mandatory path. OpenClaw-connected
messaging with WhatsApp first. Internal system activity as first-class Events. No Gmail, Outlook,
Calendar, GitHub, GitLab, Odoo, Slack, Teams or Zalo in the MVP. The adapter layer must allow them
later without blocking now.
**Consequences.** The complete loop (capture → evidence → interpretation → proposal → approval →
mutation → monitoring) can be demonstrated with one UI and one channel. The web path has no external
dependency, so Phase 2 cannot be blocked by a third party. Adding a channel must require no Work Core
change; if it ever does, the boundary is wrong and the fact gets reported rather than patched.

### ADR-0027 — OpenClaw holds two roles with two principals
**Status:** accepted.
**Context.** PQ-1 places OpenClaw in the messaging path, while ADR-0025 places it in the reasoning
path. These are different jobs with different trust properties: a channel adapter receives untrusted
text from anyone who knows a phone number; a capability runtime calls tools that change organizational
records.
**Decision.** Two ports and two identities. `ChannelAdapter` ingests Events using a **connector**
service account with ingestion rights only. `AgentRuntime` executes capabilities using an **agent**
service account with tool rights only. Neither holds the other's permissions, even when both are served
by the same OpenClaw deployment.
**Consequences.** A compromised or abused channel can inject content but cannot act on it, and the
blast radius of each identity is separately auditable. Two credentials to manage. **If OpenClaw cannot
present two distinct identities, that is a reportable conflict, not something to work around.**

### ADR-0028 — Internal activity projects into Events, with loop prevention
**Status:** accepted (Decision Pack v1.0, PQ-1 §3 and A-1).
**Context.** Internal activity is legitimate organizational signal and belongs in the timeline, but
DomainEvents already exist for state change, and naively turning every DomainEvent into an Event
creates a feedback loop: AI proposes Work → approved → DomainEvent → Event → extraction → more
proposals.
**Decision.** An allow-listed subset of DomainEvents projects into Events with `origin = internal` and
`origin_domain_event_id`. Only Events with `origin = external` are queued for AI extraction (BR-E-11).
Internal Events remain readable as AI context and usable as Evidence. `ProposalRaised`,
`ProposalApproved` and `ProposalRejected` never project.
**Consequences.** A complete activity timeline without self-amplification. The Event table carries
internal volume, which needs partitioning by origin and a retention policy. The loop-prevention filter
must be tested at both the dispatcher and the architecture level, because it is invisible when working
and catastrophic when absent.

### ADR-0029 — Project is an optional relationship on Work
**Status:** accepted (Decision Pack v1.0, A-7). **Supersedes the "triage state" proposal.**
**Context.** The earlier draft made Work without a Project a degraded triage state that could not be
scheduled, blocked or depended upon. That treats unparented work as an error to be corrected. Real
organizational work includes personal tasks, admin, operations, ad-hoc requests, cross-project work and
promises that belong to no project. Forcing a Project creates artificial structure and manual entry,
which contradicts the product's core premise.
**Decision.** `Work.project_id` is nullable, and Work without a Project is fully functional: it can be
assigned, scheduled, blocked, depended upon, linked to a Commitment and completed. Only Milestone
linkage requires a Project. No API or UI path may require a Project. AI may suggest one as a Proposal
when evidence supports it, and must never infer or invent one.
**Consequences.** Rollups must handle unparented work explicitly rather than assuming a project tree;
visibility cannot always be inherited from a Project, so Work needs its own default (team-level);
proposal routing needs a fallback for unparented work (BR-PR-04).
**Extension (Resolution Pack v1.1).** No synthetic containers: no General, Default or Unassigned Project
may be created to satisfy relational structure, by migration, seed, application default or AI proposal
(BR-P-07a). Reporting must present All Work split into Project Work and Non-project Work; project
rollups exclude non-project Work; organization, department, team and person views include both
(BR-RPT-01 to BR-RPT-05).

### ADR-0030 — Autonomy Levels 1–2 with immutable ApprovalRecords
**Status:** accepted (Decision Pack v1.0, PQ-3). **Amends ADR-0011.**
**Context.** The pre-decision draft allowed `auto_apply` for evidence attachment, entity linking, risk
narrative and summaries, with automatic promotion driven by metrics. The Decision Pack sets the MVP at
observe-and-propose plus approved execution.
**Decision.** Autonomy is `off` / `level_1_propose` / `level_2_approved_execution`, keyed per
`(organization, capability, entity_type, action)`. Level 3+ is not representable in the MVP schema.
Every AI-originated Work Core mutation is a Proposal first. Execution requires an immutable
ApprovalRecord binding approver, proposal, the exact approved action and its hash, timestamp, and the
resulting mutation. Effective authority is `agent role ∩ delegated human authority ∩ capability policy
∩ organization scope`. Promotion requires the full PQ-3 metric set plus an explicit human decision
recorded as an ADR; demotion on breach is automatic.
**Consequences.** Injected or hallucinated content can at most produce a Proposal, which is the
strongest injection control available. Review throughput becomes the product's rate limit, so the
review UI is on the critical path, not a secondary surface. Three borderline operations are referred
for decision as C-1 and run at Level 1 until answered. Capabilities can be promoted individually later
without redesign.

### ADR-0031 — Single shared Keycloak realm; org membership resolved in the application
**Status:** accepted (Decision Pack v1.0, PQ-2). **Amends ADR-0007. Resolves S-1 towards option (a).**
**Context.** Realm-per-organization gives strong IdP-level isolation but couples the domain model to a
tenancy mechanism, complicates cross-org membership, and makes the MVP's single organization
disproportionately expensive to operate.
**Decision.** One product-level realm. Keycloak authenticates and carries coarse platform roles. The
application resolves `subject → Person → OrganizationMembership → RoleAssignment → permissions`. Active
organization is established per request and validated against membership, never inferred from a claim
alone. Isolation rests on application authorization plus RLS, both enforced independently.
**Consequences.** The domain model has no realm dependency, and multi-org membership is expressible.
The application, not the IdP, is the isolation boundary, which raises the stakes on the tenant isolation
suite: it must pass with application scoping disabled, proving RLS holds alone.

### ADR-0032 — `WorkAssignment` is the canonical assignment source of truth
**Status:** **accepted** (Resolution Pack v1.1). Supersedes M-1 option (a) and the "denormalised mirror"
formulation proposed earlier.
**Context.** Decision Pack v1.0 named `WorkAssignment` as an org-scoped entity without fixing its
semantics. The interim reading kept `work.assignee_person_id` as a service-maintained mirror, which is
the standard way this pattern rots: two writable representations of the same fact, drifting the first
time someone updates one of them in a migration or a bulk operation.
**Decision.** `WorkAssignment` is canonical and is the only writable assignment surface. Work carries no
assignee column and no collaborator array. A Work may have zero, one or many assignments. Roles are
`OWNER` (zero or one active), `CONTRIBUTOR` (zero or more) and `REVIEWER` (zero or more). Work with no
assignment is a valid steady state, required for AI capture of statements like "someone please check the
IOC API before Friday". Fast access to the current owner is a view, projection or cached read model, not
a column.
**Consequences.** Assignment history is queryable and attributable, which matters for accountability and
for AI-proposed reassignment. Every query that used to read one column now joins or reads the
`work_current_owner` view, so that view needs an index strategy from the first migration. The
single-active-OWNER rule must be a partial unique index; enforcing it only in application code would
leave it defeatable by any other write path. Notification and proposal routing gain an explicit
"no owner" branch (BR-PR-04, MON-002a).

### ADR-0033 — Comments are Events, not a Comment aggregate
**Status:** accepted (Resolution Pack v1.1, N-3).
**Context.** PQ-1 lists comments among internal activity, and the earlier draft flagged that no Comment
entity existed anywhere. The options were a full Comment aggregate or reuse of the Event model.
**Decision.** No Comment entity in the MVP. A comment is `Event.type = COMMENT` with
`origin = INTERNAL`. The Event type enum must support at least `EXTERNAL_MESSAGE`, `MANUAL_CAPTURE`,
`MEETING_NOTE`, `COMMENT`, `ATTACHMENT`, `SYSTEM_ACTIVITY`. A Comment aggregate is introduced only when
threaded discussion, mentions, reactions, subscriptions or moderation are actually required, all of
which are out of MVP scope.
**Consequences.** One fewer aggregate, and comments inherit Event immutability, provenance and evidence
linking for free, which is genuinely the right shape for activity data. Editing and deleting comments
are not free operations: under BR-E-01 an Event is immutable, so a corrected comment is a new Event.
That is a product behaviour worth confirming with design before the UI is built. Because comments are
internal-origin, BR-E-11 currently excludes them from extraction; whether that is intended is **N-4**,
open.

### ADR-0034 — Persistence, not computation, is what requires autonomy policy
**Status:** accepted (Resolution Pack v1.1, C-1).
**Context.** Three operations sat on the line between "AI worked something out" and "AI changed the
record": attaching Evidence to an existing entity, writing narrative onto a monitor-detected Risk, and
summarising. Treating all computation as a mutation would make the product unusable; treating all of it
as free would let AI-authored interpretation enter the record unreviewed.
**Decision.** The boundary is persistence as business state, not computation. AI may calculate, explain
and summarise freely. Attaching Evidence to an existing entity is Level 1. Persisting a Risk narrative
is Level 1. Deterministic Risk detection is not an AI mutation at all. A summary that is not persisted
as business state needs no Proposal; the moment it is persisted durably or becomes Work Core state, it
follows the normal Proposal and Approval path.
**Consequences.** A clean, statable test for every future capability: does anything read this as
business truth? The pressure point is caching. A derived, invalidatable cache is not persistence; a
column a human reads in a status review is, whatever it is called. The Definition of Done asks for that
distinction explicitly so it is argued in review rather than discovered later.

## 3. Template for promoted records

```markdown
# ADR-nnnn — <title>

Status: proposed | accepted | superseded by ADR-mmmm
Date: YYYY-MM-DD
Deciders: <names>

## Context
What forces are at play, what constraints apply, what makes this non-obvious.

## Options considered
1. <option> — pros, cons
2. <option> — pros, cons

## Decision
What we chose, stated so it can be checked.

## Consequences
What becomes easy, what becomes hard, what must now be tested or monitored.

## Review trigger
The condition that should make us revisit this.
```

### ADR-0035 — Cross-context references are validated through the published interface, never by the foreign key
**Context.** A Project names an owning Team, a lead Person and a sponsor Person; those rows belong to
Identity, not to Work Core. Until now nothing checked them: the write went out, PostgreSQL refused it
on a composite foreign key, and the API rendered the violation as a generic 422 that named nothing
(W-11). The foreign key is doing real work — it makes a cross-tenant reference unrepresentable — but
it is a backstop, and a backstop is not a validation layer. It cannot say *which* field was wrong, it
fires after the transaction has done its work, and it ties the application's error vocabulary to
whatever psycopg happens to raise.

The tempting shortcut is for Work Core to query `person` and `team` itself. Both tables are in the
same database and a `SELECT` would work. That is precisely the coupling ADR-0001 exists to prevent:
the moment Work Core reads Identity's tables, Identity cannot change a column without breaking a
context that never declared a dependency on it.

**Decision.** Work Core validates every Identity reference by calling `app.contexts.identity.public`
before it writes. Identity publishes reference predicates — `person_exists`, `team_exists`,
`department_exists` — and Work Core calls them; it never queries Identity's tables and never imports
anything behind `public`.

The dependency runs one way: **Work Core depends on Identity; Identity depends on nothing.** Identity
answers questions about people and structure and asks nothing about work, which is why it can be read
by every other context without creating a cycle. `test_identity_never_imports_the_work_core` and
`test_the_cross_context_exception_list_stays_one_directional` enforce the direction, and the
import-linter `context-isolation` contract permits exactly one edge:
`app.contexts.work.* -> app.contexts.identity.public`.

A reference that does not resolve is a `DomainRuleViolation` carrying the rule id, raised before any
row is written.

**Consequences.** Errors name the field and the rule instead of the constraint. The foreign keys stay
exactly as they are — two independent mechanisms, neither of which is the excuse for dropping the
other, the same shape as application scoping and RLS (BR-G-01a). One extra query per referenced field
on write paths, which is not a read path and is not hot. Extracting Identity into its own service
later remains feasible because the call already goes through an interface rather than a join.
Rejected: Work Core querying `person` directly (couples the contexts); relying on the foreign key
alone (W-11, the state this replaces).

### ADR-0036 — People are provisioned administratively; there is no just-in-time creation
**Context.** One Keycloak realm serves every organization (ADR-0031), and `keycloak_subject → Person
→ OrganizationMembership → RoleAssignment` is resolved in the application (security-model §2). Phase
1 scope says "Person provisioning" without saying what it means, which left W-13 open: somebody with
a valid realm token and no Person row gets a 404, correctly and unhelpfully.

The alternative is creating a Person on first sign-in. It is convenient and it is an authorization
decision in disguise: the realm is shared, so *any* authenticated subject could conjure themselves a
Person in *any* organization they name in a header. Whichever organization that is, and whatever role
they land with, is a grant nobody made.

**Decision.** A Person exists because somebody with `PERSON.CREATE` created them. There is no
just-in-time path and no self-service path. Signing in with a subject that has no Person in the
requested organization is a 404 — the same 404 as an organization that does not exist, because
whether one exists is itself tenant information (contract §5).

Linking the Keycloak subject is part of creating or updating the Person, and `keycloak_subject` stays
unique per organization rather than globally: one human working for two organizations is two Person
rows sharing a subject (security-model §2, PQ-2). A Person may also have no subject at all — they can
be assigned work and be named as a committer without ever signing in (BR-I-04).

**Consequences.** Onboarding is a deliberate act with an audit entry naming who performed it, which
is what an organization actually wants. A new hire cannot self-serve; that is the point. Development
and the E2E harness seed people through `ops/dev/seed.py` rather than by logging in. If invitation or
domain-verified self-registration is wanted later it is a new decision with its own approval flow,
not a default that arrived by omission. Rejected: JIT provisioning on first login; provisioning from
a realm group claim, which would put authorization back in the token that ADR-0031 took it out of.

### ADR-0037 — `ExternalIdentity` is its own authorization resource, and confirmation is not self-service
**Context.** An `ExternalIdentity` maps a handle in another system — a phone number first (PQ-1,
WhatsApp) — to a Person. BR-I-06 says a mapping below 0.90 confidence or without `confirmed_at` must
not be used to attribute ownership, assignment or commitment authorship; BR-I-07 makes
`(source_system, external_id)` unique per organization. The authorization matrix had no resource type
for it at all.

Two shapes were available. Fold it into `PERSON`, treating a handle as an attribute of the person it
belongs to — simple, no new cells, and it inherits `PERSON.UPDATE`'s `SELF` grant, which would let
anybody attach handles to their own record. Or give it a resource type of its own.

The `SELF` grant is what settles it. This mapping is the mechanism by which an inbound message is
attributed to a human, so a confirmed mapping is an authority to speak as somebody. Self-service
confirmation would mean claiming a number is the same act as being believed about it.

**Decision.** `EXTERNAL_IDENTITY` is a resource type. Reading is organization-wide, because
attribution has to be explicable to the people it affects. Creating, updating and changing state are
`org_admin` only. There is no `SELF` grant on any of its actions.

A mapping is created unconfirmed. Confirming it is `CHANGE_STATE` and records who confirmed it and
when, which is the provenance BR-I-06 depends on. What *raises* confidence — a resolution algorithm,
an invitation flow, a channel handshake — is PQ-7 and is not decided here; this decides only who may
persist the answer.

**Consequences.** Three new matrix rows, and the coverage test forced every one of the seven roles to
be named in each. Phase 3 channel work inherits a resource whose permissions already exist rather
than inventing them under deadline. A person who changes their phone number needs an administrator,
which is friction, and is the correct amount of friction for a change that moves who the system
believes somebody is. Rejected: governing it through `PERSON` (inherits a `SELF` grant that must not
exist here); leaving it unmodelled until Phase 3 (W-12 would stay open and the table would stay
orphaned).

### ADR-0038 — Event immutability is a column-scoped database trigger, not a convention

**Context.** BR-E-01 says an Event is immutable after `received`, *except* for `processing_status`,
participant resolution and retention fields. That exception is what makes the rule hard. `audit_entry`
could be made append-only with a trigger that refuses every UPDATE, because nothing about an audit row
is ever allowed to change. An Event is different: extraction will move `processing_status` from
`received` to `extracted`, participant resolution will fill in `person_id` as confidence improves, and
a retention sweep will set `retention_expires_at` and blank `raw_payload_uri`. A blanket refusal would
make the entity unusable; no refusal at all makes "immutable" a comment rather than a control.

Enforcing it in the service layer was the alternative and it fails the same way every application-layer
invariant fails: it holds only for code that goes through the service. The whole point of an Event is
that it is the record of what happened, and a record that a future maintenance script can quietly edit
is not a record. The extraction worker, the retention job and the projector are all still to be
written, and each is a plausible place for an UPDATE that nobody reviews closely.

**Decision.** A `BEFORE UPDATE ... FOR EACH ROW` trigger on `event` compares OLD and NEW and raises
`restrict_violation` if any column outside an explicit mutable allow-list has changed. DELETE and
TRUNCATE are refused outright. The allow-list is exactly the three categories BR-E-01 names:

* `processing_status`, `processing_error` — the extraction pipeline's own state
* `participant_count` — a denormalised count maintained as participants resolve
* `retention_expires_at`, `raw_payload_uri`, `deleted_at` — BR-E-07 retention and purge

Everything else — `occurred_at`, `body_text`, `type`, `origin`, `source_system`, `source_ref`,
`content_hash`, `sensitivity` — is frozen the moment the row is inserted. A correction is a new Event
carrying `revision_of_event_id`, which is BR-E-02's revision path and is the only way the record of
what was originally observed can change meaning.

`EventParticipant` gets the same treatment with a different allow-list: `person_id` and
`match_confidence` are mutable because resolution is a process (BR-I-06, BR-E-12); `external_handle`
is frozen, because BR-E-12 requires the raw sender identifier to survive resolution unchanged.

**Consequences.** "Immutable" becomes checkable — `test_event_immutability.py` asserts it with raw SQL
as the owner role, so no application code is involved in the proof. A future field must be classified
as frozen or mutable when it is added, which is a small tax on every migration touching `event` and is
the tax that keeps the rule honest. The error surfaces as a database exception rather than a domain
rule violation, so the service layer still validates first and the trigger is the backstop, in the same
two-mechanism arrangement BR-G-01a already describes for tenancy. Rejected: application-only enforcement
(does not survive a worker or a psql session); a blanket append-only trigger (contradicts BR-E-01's
stated exceptions); an `event_revision` side table (duplicates what `revision_of_event_id` already says
and splits the history across two shapes).

### ADR-0039 — Attachment bytes never pass through the API; an ObjectStore port fronts MinIO

**Context.** The web capture surface lets a user attach files to an Event (BR-E-14). The architecture
already fixes where the bytes live — MinIO, never Postgres — and already says the agent runtime reads
blobs "only through short-lived presigned URLs issued by the API after authorization". What it does not
fix is the *upload* direction, or how the application code talks to the store at all.

Streaming uploads through the API is the obvious first answer and is the wrong one at this scale: it
puts request-duration memory and connection pressure on the same process that serves every read, and
it makes a 200 MB attachment a web-tier problem. The counter-risk is real though — a presigned PUT
hands the client a window in which it writes whatever it likes, so `size_bytes`, `media_type` and
`checksum` recorded at request time are claims, not facts.

Testing is the second half of this. MinIO is on the `data` network with no exposed port, and the
existing suite deliberately depends on no external service — the OIDC tests generate their own realm
rather than requiring Keycloak. An attachment path that can only be tested with MinIO running would be
the first piece of this system whose tests need infrastructure.

**Decision.** Two parts.

*The port.* `app.platform.storage` defines `ObjectStore`, a protocol with `presigned_put`,
`presigned_get`, `stat` and `delete`. `S3ObjectStore` implements it over MinIO/S3; `InMemoryObjectStore`
implements it for tests. Services depend on the protocol. This is not a second object-storage
abstraction — it is the seam to the one the architecture already chose, and it exists so that the
capture path is testable without infrastructure, the way `test_realm()` already makes OIDC testable
without Keycloak.

*The flow.* Attaching a file is two calls and three states. `POST /events/{id}/attachments` authorizes
against the **Event**, records a `pending` row with the client's claimed filename and media type, and
returns a presigned PUT valid for minutes. The client uploads directly to the store. `POST
/events/{id}/attachments/{attachment_id}/complete` re-authorizes, calls `stat` on the object, and
records the size and checksum the **store** reports — never the client's numbers — moving the row to
`available`. A row that never completes stays `pending`, is excluded from reads, and is a retention
sweep's problem rather than a correctness problem.

Downloads are symmetrical: `GET /events/{id}/attachments/{attachment_id}/content` authorizes against
the Event, then issues a short-lived presigned GET and returns it. The object key is derived
server-side from `(org_id, event_id, attachment_id)` and is never accepted from the client, so a
presigned URL cannot be requested for an object the caller has not been authorized to reach.

**Consequences.** Attachment authorization is Event authorization by construction — there is no
attachment permission to get wrong, because every attachment endpoint loads the Event first and every
denial is the Event's denial. Bytes never touch the API process or Postgres. The recorded integrity
data is the store's, so a client that lies about its upload produces a row that disagrees with the
object and is caught at completion rather than trusted forever.

The presigned window is the residual risk and it is deliberate: within it, whoever holds the URL can
write those bytes. It is scoped to one key, expires in minutes, and is issued only to a caller who has
already passed Event authorization. A leaked URL is a leaked object, not a leaked bucket.

Rejected: proxying bytes through the API (moves large-object load onto the web tier for no security
gain, since authorization happens before the URL is issued either way); trusting client-reported size
and checksum (makes the integrity fields decorative); a separate `ATTACHMENT` authorization resource
(invites the two permissions to drift apart, which is exactly the bug this design makes
unrepresentable).
