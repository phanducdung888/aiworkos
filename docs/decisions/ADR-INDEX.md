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
| 0040 | Context dependency order is a DAG; Intelligence is the only context that reaches across | accepted | arch §4, ADR-0001 |
| 0041 | Approval binds to a canonical action hash; execution recomputes it | accepted | BR-AI-18, BR-PR-01 |
| 0042 | The Tool Gateway is a registry of named application-service calls, not a SQL surface | accepted | ai §1, BR-AI-16 |
| 0043 | AI origin is derived from the authenticated principal, never from a request field | accepted | BR-AI-03, CP7 risk |
| 0044 | The job queue is a PostgreSQL table claimed with SKIP LOCKED, not Redis | accepted | arch §3, BR-PR-01 |
| 0045 | The agent layer is a package above the contexts and may import only published interfaces | accepted | ADR-0002, ADR-0040 |
| 0046 | The worker holds a dedicated database role; default-deny is restored for everyone else | accepted | ADR-0044, BR-G-01a |
| 0047 | Agent capability policy is a tenant-scoped table, deny by default | accepted | BR-AI-30, BR-AI-03 |
| 0048 | Approved execution has exactly one path, and it is the queue | accepted | ADR-0044, BR-PR-01 |
| 0049 | The provider contract is typed, and a provider has no authority | accepted | ai §12, ADR-0045 |
| 0050 | Confidence carries its source; an unmeasured confidence is UNKNOWN | accepted | BR-AI-09 |
| 0051 | The execution deadline is derived from the approval, not stored | accepted | BR-AI-22, ADR-0041 |
| 0052 | An agent produces ToolIntents; WorkOS decides what becomes a Proposal | accepted | ADR-0042, BR-AI-16 |
| 0053 | OpenClaw: spike further, do not integrate | accepted | A-3, ADR-0027 |
| 0054 | External identity resolution is lookup-only and attribution is threshold-gated | accepted | PQ-7, BR-I-06, BR-AI-34 |
| 0055 | The model quotes a deadline phrase; WorkOS reads it against the Event | accepted | BR-C-05, BR-AI-17 |
| 0056 | Provenance is a walk, not a denormalised column | accepted | BR-PR-08, ADR-0041 |
| 0057 | The policy surface is derived from the tools, and a cell's action decides | accepted | ADR-0047, BR-AI-30 |
| 0058 | Ingestion has no in-process port; the connector boundary is the capture API | accepted | BR-E-02, ADR-0054 |
| 0059 | OpenClaw publishes no outbound delivery contract; the integration direction reverses | accepted, supersedes part of 0053 | A-3, ADR-0027, ADR-0053 |
| 0060 | An ingestion-only role, holding one grant | accepted | ADR-0027, ADR-0058, migration 0014 |
| 0061 | Email over IMAP is the first connector; PQ-1 amended | accepted, amends Decision Pack v1.0 | PQ-1, ADR-0058, ADR-0059 |
| 0062 | A connector is an untrusted edge, confined and tested like one | accepted | ADR-0002, ADR-0058, ADR-0061 |
| 0063 | Attachment delivery by a connector | accepted (CP23) | ADR-0039, ADR-0060, ADR-0062 |
| 0064 | Tenant scoping is not authorization | accepted (CP24) | ADR-0008, ADR-0027, ADR-0060 |
| 0065 | The connector's per-message ceiling, measured | accepted (CP24) | ADR-0058, ADR-0061, ADR-0063 |
| 0066 | The connector obtains and renews its own credential | accepted (CP25) | ADR-0058, ADR-0060 |
| 0067 | A presigned URL is signed for the client that will use it | accepted (CP25) | ADR-0039, ADR-0063 |
| 0068 | The organization is named by the person, in the browser | accepted (CP25) | ADR-0008, ADR-0031 |

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

### ADR-0040 — Context dependency order is a DAG, and Intelligence is the only context that reaches across

**Context.** Checkpoints 5.1 and 6 each added one cross-context edge and each was easy to justify in
isolation. Checkpoint 7 adds Commitment and Intelligence, and Intelligence has to call Work Core to
create Work, Commitment to create a promise, Signal to read the Evidence that justifies both, and
Identity to check that the person named exists. That is four edges from one context, which is either
the beginning of a dependency graph nobody can hold in their head or a shape that needs stating.

**Decision.** Contexts are ordered, and every edge runs down the order, through `public` only:

```
identity  →  signal  →  work  →  commitment  →  intelligence
```

Each context may import the published interface of any context to its left and none to its right.
Identity depends on nothing. Intelligence is the only context with several edges, and that is not an
exception to the rule but what the rule is for: *executing an approved Proposal is precisely the act
of reaching across contexts*, so the reaching is concentrated in one place where it can be reviewed,
rather than distributed as a convenience wherever somebody needed it.

The order is not arbitrary. It is the direction facts flow: something is observed (Signal), somebody
decides what to do about it (Work), somebody promises it (Commitment), and a proposal to change any
of those is reviewed and executed last (Intelligence). A Commitment references Work because a promise
can be fulfilled by work; Work does not reference Commitment, because work exists whether or not
anybody promised it.

**Consequences.** `.importlinter` lists every edge explicitly and `test_context_dependencies_form_a_dag`
recomputes the order from the configuration and fails on any edge that runs the wrong way — so adding
a backwards edge fails the build rather than merely being discouraged. A new context has to be placed
in the order when it is created, which is the moment the question is cheapest to answer.

The cost is real: Work Core cannot ask "is there a Commitment fulfilled by this Work". That query
belongs to a read model or to Intelligence, and the day it is genuinely needed is the day to build one
rather than to reverse an edge. Rejected: a shared kernel holding cross-context types (becomes the
place every context reaches into, which is the coupling with extra steps); events-only communication
between contexts (the approved-execution path must be synchronous and transactional, because BR-PR-01
requires the mutation and its ApprovalRecord to agree).

### ADR-0041 — Approval binds to a canonical action hash, and execution recomputes it

**Context.** BR-AI-18 requires that an approved mutation be *the exact action that was approved*, and
BR-PR-02 requires a Proposal whose target changed to be re-presented rather than applied blindly. The
failure being prevented is specific: a human reads "assign this to Mai, due Friday", approves it, and
something between approval and execution — an edit, a race, a retry against a changed row, a bug —
causes a different mutation to run under that approval. The approval is real, the authority is real,
and the action is not the one anybody agreed to.

Storing the approved action as JSON and comparing objects at execution time is the obvious approach and
is not enough. Two JSON documents that differ only in key order or number formatting are the same
action; two that differ in a nested value are not, and an equality check written by hand tends to
compare the fields somebody remembered.

**Decision.** Every Proposal carries an `action`: a tool name, a tool version and fully resolved
arguments. Its canonical form is JSON serialised with sorted keys, no insignificant whitespace, and
UUIDs and dates as strings; `action_hash` is the SHA-256 of that form. Approving writes an immutable
ApprovalRecord holding `approved_action` and `approved_action_hash`.

The Tool Gateway recomputes the hash from the approved action at execution time and refuses to run
unless it matches the record. Two independent things therefore have to agree: the bytes that were
approved, and the digest taken when they were approved.

Revising a Proposal produces a new action and a new hash, which cannot match any existing
ApprovalRecord — so a modified action requires a new approval by construction rather than by a check
somebody has to remember to write. Approving with edits is the same mechanism: the ApprovalRecord holds
the *edited* action as approved, the diff is retained (BR-PR-06), and the hash is of what the approver
actually agreed to, never of what was originally proposed.

**Consequences.** "Approval cannot be reused for a different action" becomes a property of the data
rather than a rule in a service. Execution is idempotent at the record: `execution_status` moves from
`pending` exactly once under a conditional update, so a retried execute finds the record already
executed and returns the original outcome instead of running the mutation twice.

The canonicalisation is now load-bearing and has its own tests: a change to how actions are serialised
would silently invalidate every stored approval. That is the cost, and it is why the format is fixed
here rather than left to whatever `json.dumps` does by default. Rejected: comparing the action objects
field by field (compares what the author remembered); hashing the Proposal row (the row carries mutable
status and review fields, so its digest would change for reasons that have nothing to do with the
action).

### ADR-0042 — The Tool Gateway is a registry of named application-service calls, not a SQL surface

**Context.** BR-AI-16 says there is no code path by which AI mutates Work Core without an approved
Proposal, and ADR-0002 says AI has no database access at all. Those are constraints on a component
that does not exist yet — no LLM, no agent runtime — and the temptation at this checkpoint is to build
an execution path general enough to be convenient later, which is how a generic "apply this change"
executor gets written and how arbitrary mutation arrives through the back door.

**Decision.** The Tool Gateway is a closed registry. Each entry maps a tool name and version to one
function that calls exactly one existing application service, with a typed argument schema. There is no
generic executor, no field-path applier, no SQL, and no way to name an operation the registry does not
contain — an unknown tool is a refusal, not a fallback.

Every tool is a *call into an existing service*, never a reimplementation. Creating Work through the
Gateway runs the same `WorkService.create` a human request runs, so BR-W rules, reference validation
(ADR-0035), audit and the outbox all happen because they already happen there. A tool that duplicated
that logic would be a second implementation of the rules, drifting from the first.

Execution is attributed to the approving Person (BR-AI-19), with `executed_via`, `proposal_id` and
`approval_record_id` on the actor. The approver's own authority is what authorizes the mutation — the
service authorizes normally, so approving never grants permission the approver lacks (BR-PR-05).

The MVP registry holds only what Checkpoint 7 needs, and the operations BR-AI-06 and BR-AI-23 forbid
have no entry at all. There is nothing to disable and no flag to get wrong: deletion, cancellation,
membership, roles and outbound messaging are absent, and a Proposal naming one is refused at creation
because the tool does not exist.

**Consequences.** The set of things an approved Proposal can ever do is enumerable by reading one file,
and `test_the_registry_contains_no_forbidden_operation` fails if a future entry names one. Adding a
capability is deliberate: a registry entry, an argument schema, a matrix row if a new resource is
involved, and a test.

The limitation is that a Proposal can only ever express what some tool already does, so a genuinely new
kind of change needs a code change rather than a cleverer payload. That is the intended trade: this
boundary exists to be narrow, and a boundary that can express anything is not a boundary. Rejected: a
generic field-path executor driven by `ProposedChange` rows (expressive enough to bypass any rule not
independently enforced); letting the Gateway open its own transaction and write directly (the second
mutation path ADR-0002 exists to prevent).

### ADR-0043 — AI origin is derived from the authenticated principal, never from a request field

**Context.** Checkpoint 7 left `raised_by_ai` as a parameter on `RaiseProposal`. Two rules key off
it: BR-AI-02 requires an AI-originated Proposal to cite Evidence, and BR-C-03 requires an AI-sourced
Commitment to do the same. Nothing exploited it, because every path through the API was a human and
the request schema never exposed the flag — but it was a security property held up by the fact that
nobody had wired the other case yet. The moment an agent has a credential, "is this AI-originated"
becomes a question the caller answers about itself.

The general shape of the bug is worth naming, because it recurs: a privilege or an obligation that
travels in the payload rather than in the identity. A caller that can set the field can choose which
rules apply to it. Here the flag carries an *obligation* rather than a permission, so setting it
falsely would mean claiming to be AI and accepting more scrutiny — but the inverse is the attack: an
agent that omits it escapes the evidence requirement entirely, and BR-AI-02 becomes advisory.

**Decision.** `raised_by_ai` is deleted as an input. AI origin is read from the `Actor` on the
service context: `actor.type is ActorType.AI`, which the `Actor` constructor already refuses to
produce without an `ai_interaction_id` and a delegated `person_id` (BR-AI-02, BR-AI-03). The same
rule applies to `produced_by_ai` on Commitment creation.

An `Actor` of type AI can only be built by the agent runtime, from an `AgentPrincipal` that was
authenticated and whose authority was intersected (ADR-0045). There is no request header, body field
or query parameter anywhere in the system that produces one, and `test_no_request_field_can_claim_ai_origin`
walks the API schemas to keep it that way.

**Consequences.** The evidence requirement becomes unavoidable rather than self-declared: an agent
cannot raise an evidence-free Proposal by omitting a flag, because the flag does not exist and its
actor says what it is. A human cannot accidentally trigger AI-path rules either, which removes a
class of confusing refusal.

The cost is that an AI-originated write must now carry a real `ai_interaction_id` all the way down,
so the interaction has to be created before any of its output. That ordering is a constraint on the
runtime and is the correct one — an interaction that produced Proposals and left no record of itself
is precisely what BR-PR-08 exists to prevent. Rejected: validating the flag against the actor and
refusing a mismatch (still two sources of truth, one of which is attacker-controlled); a trusted
header set by an internal proxy (moves the trust boundary to network position, which is not an
authentication mechanism).

### ADR-0044 — The job queue is a PostgreSQL table claimed with SKIP LOCKED, not Redis

**Context.** Approved Proposals must execute outside the HTTP request. The requirement is exactly
once: an approval authorises one mutation (BR-AI-20), and a queue that delivers twice must not
produce two Work items. Redis is already running in Compose and is the obvious queue.

It is the wrong choice here, for one reason that outweighs its convenience. The mutation, the
ApprovalRecord's outcome fields, the audit entry and the outbox row all commit in one PostgreSQL
transaction (BR-PR-01). If the job's completion lives in Redis, the two can disagree: the
transaction commits and the acknowledgement is lost, so the job runs again; or the acknowledgement
lands and the transaction rolls back, so the approval is stranded as claimed-but-unexecuted with
nothing left to retry it. Every fix for that is a distributed-commit protocol between two systems,
which is a large amount of machinery to avoid using the transactional database already present.

**Decision.** A `job` table, claimed with `SELECT ... FOR UPDATE SKIP LOCKED` inside the same
transaction that performs the work. Claiming, executing, and marking the job done are one commit, so
a crash at any point leaves the job unclaimed and retryable, and a success leaves it unambiguously
finished. `SKIP LOCKED` is what makes multiple workers safe without a lock service.

Delivery is still at-least-once, because a worker can die after committing and before anything
observes it. Exactly-once *execution* comes from the layer below: `claim_for_execution` moves an
ApprovalRecord out of `pending` with a conditional update, so a redelivered job finds the approval
already spent and completes without running the mutation again. The queue guarantees the work is
attempted; the approval guarantees it happens once. Neither alone is sufficient and the composition
is the design.

Redis stays in the stack for caching and ephemeral state, where losing a value is a performance
event rather than a correctness one.

**Consequences.** No new infrastructure, no second durability story, and a queue that can be
inspected with the same SQL as everything else — a stuck job is a row somebody can read. Throughput
is bounded by PostgreSQL rather than by Redis, which at MVP volumes is not a constraint worth
designing around; the day it is, the claim interface is narrow enough to put something else behind.

The visible cost is polling: a worker with nothing to do wakes, runs one cheap indexed query, and
sleeps. `LISTEN/NOTIFY` would remove the idle query and is deliberately not used yet, because it
adds a delivery path that has to be correct *in addition to* the polling one rather than instead of
it. Rejected: Redis with a Lua acknowledgement script (still two systems, still no shared
transaction); an in-process background task (loses everything on restart, which is not a queue).

### ADR-0045 — The agent layer is a package above the contexts and may import only published interfaces

**Context.** ADR-0002 says AI has no database access and the Tool Gateway is the only mutation path.
Until now that was a statement about a component nobody had written. Checkpoint 8 writes it, and the
question becomes concrete: what stops the agent code from importing a repository, opening a session
and writing a row — not maliciously, but because it was convenient at 6pm and the import was there.

A convention does not stop it. Neither does a code review six months from now, on a diff that does
one useful thing and one careless one.

**Decision.** The agent lives in `app/agent`, a package placed above `app.contexts` in the layer
contract, and constrained by three forbidden-import rules rather than by intent:

* `app.agent` may not import `app.platform.db`, `sqlalchemy`, or any context internal — only
  `app.contexts.*.public`. It has no way to name a repository, a model or a session.
* `app.agent.providers` may not import `app.contexts` at all. A provider adapter talks to a model
  and returns structured output; it has no business knowing what an Event is.
* The Tool Gateway remains the only path to a mutation, and the agent reaches it exactly as a human
  request does — by raising a Proposal that a person approves.

The runtime receives a session-shaped dependency it cannot construct and cannot reach through: what
it holds is an `AgentRuntimeContext` exposing published services, not a `Session`.

**Consequences.** "AI cannot write to the database" is enforced by the build. `test_the_agent_layer_cannot_reach_the_database`
walks the AST of every module under `app/agent` and fails on a forbidden import, so the violation is
caught in the commit that introduces it rather than in an incident. A provider adapter for a new
model vendor is a file in `app/agent/providers` that cannot, structurally, do anything but call a
model.

The cost is indirection: the agent must go through published interfaces even when a direct query
would be shorter, and anything it needs that is not published has to be published deliberately.
That is the intended friction — the alternative is a layer whose access is decided case by case by
whoever is writing it. Rejected: putting the agent inside `app.contexts.intelligence` (inherits that
context's internals, including its repository); a separate service over HTTP (a network boundary
where a module boundary suffices, and the MVP does not need the deployment complexity).

### ADR-0046 — The worker holds a dedicated database role; default-deny is restored for everyone else

**Context.** Checkpoint 8 gave the `job` table a policy permitting SELECT and UPDATE when no
organization context is set, because a worker cannot know which tenant has work before it looks and
no role in this system holds BYPASSRLS. It worked, and it was the wrong shape: the exemption was
keyed on *the absence of a setting*, so any session that forgot to scope — including one serving an
HTTP request — could read the queue. The control protecting every other table was switched off by
the same mistake it exists to catch.

What leaked was modest (job kinds, approval ids, which organizations had work) and the mechanism was
not: a condition that grants access when a variable is unset is the opposite of default-deny.

**Decision.** The exemption moves from a *state* to an *identity*. A third database role,
`workos_worker`, holds a policy on `job` and on nothing else:

```sql
CREATE POLICY job_worker_claim ON job
  USING (current_user = 'workos_worker' OR org_id = app_current_org())
```

`workos_app` and `workos_owner` get strict tenant isolation back on `job`, exactly like every other
table, and `test_an_unscoped_session_sees_nothing` covers the queue again. The worker process is the
only thing that connects as `workos_worker`; it claims a job, scopes the session to that job's
organization, and everything the handler then touches is subject to the ordinary policies.

The role is `NOSUPERUSER NOBYPASSRLS NOCREATEROLE`, so its reach is one policy on one table. On every
other table it is as constrained as the application role, which is what makes "the worker scoped
itself correctly" a property the database enforces rather than something the loop remembers.

**Decision on global jobs.** A job outside tenant scope would be infrastructure rather than
business, and none exists. `job.org_id` stays `NOT NULL`, so the category cannot be created by
accident; introducing one means a migration, a null-scope policy and an explicit decision about what
a global job may see — which is the conversation worth being forced into.

**Consequences.** The blast radius of a forgotten `set_config` returns to nothing. An attacker
reaching the application role gets no queue visibility, and reaching the worker role gets the queue
and nothing else. The cost is a third credential to provision and rotate, and a worker that must be
deployed with it — a real operational cost, paid to remove a conditional that granted access on
absence. Rejected: keeping the unscoped-read policy with a test pinning it to one table (pins the
blast radius, not the mechanism); BYPASSRLS for the worker (removes isolation on *every* table to
solve a problem on one); a per-organization polling loop (needs an unscoped read of `organization`,
which is the same exemption moved one table over).

### ADR-0047 — Agent capability policy is a tenant-scoped table, deny by default

**Context.** BR-AI-30 requires autonomy to be stored per `(organization, capability, entity_type,
action)` and forbids a global flag. Checkpoint 8 shipped two module-level constants instead —
`AGENTS` and `DEFAULT_POLICY` — so every organization had the same policy and changing it was a
deployment. Safe, because the constants were restrictive, and wrong in a way that gets worse: the
first customer who wants extraction off is a code change, and the second who wants it on for one
team has nowhere to put that.

**Decision.** `agent_capability_policy`, one row per `(org_id, capability, entity_type, action)`,
holding `mode` ∈ `off | level_1_propose | level_2_approved_execution`. Tenant-scoped with RLS like
everything else.

**Absence is denial.** There is no default row, no seed and no fallback: a capability with no policy
for an organization is `off`. That is the opposite of how permission tables usually drift — a
missing row is normally read as "no restriction" — and it is chosen because the failure modes are
not symmetric. A capability that is off when it should be on is a support ticket; one that is on
when it should be off is an AI writing into somebody's organization without anybody having decided
it should.

**Policy narrows and never grants.** The effective authority stays an intersection:

```
agent capability ∩ delegated human authority ∩ capability policy ∩ organization scope
                 ∩ forbidden actions/resources
```

A policy row naming a capability the agent does not have grants nothing; a policy row naming an
action the delegating person cannot perform grants nothing. `FORBIDDEN_ACTIONS` and
`FORBIDDEN_RESOURCES` are applied last and are not policy-configurable at all — membership, roles and
approval cannot be switched on by a row.

**Agents cannot change it.** `AGENT_CAPABILITY_POLICY` is not in the tool registry, has no
application service reachable from `app/agent`, and `ROLE_ASSIGNMENT`-style resources are already in
`FORBIDDEN_RESOURCES`. An agent that could widen its own policy would make every other control
advisory.

**Consequences.** Autonomy becomes an organization's decision, recorded with an audit entry naming
who made it, which is what BR-AI-32's promotion process needs to point at. Evaluation is a pure
function over rows and is testable without a model. The cost is that a new organization starts with
every capability off and somebody has to turn them on — deliberate friction at exactly the moment
the decision should be made rather than inherited.

### ADR-0048 — Approved execution has exactly one path, and it is the queue

**Context.** Checkpoint 8 added the queued path and left the synchronous one in place, because the
Checkpoint 7 tests exercised it and removing it was not in scope. Two ways to perform the same
mutation is one more than a design wants, and the cost is not theoretical: every future safety
property — rate limiting, a circuit breaker, an execution window, a kill switch — has to be
implemented twice or it is implemented once and bypassable.

**Decision.** `POST /approvals/{id}/execute` is gone. Approving writes the ApprovalRecord;
`POST /approvals/{id}/queue` enqueues; the worker executes. The HTTP layer has no code path that
performs an approved mutation, and `test_no_synchronous_execution_path_remains` walks the routers to
keep it that way.

Approval and execution stay separate transactions, which is what made the split worth having: the
ApprovalRecord is durable before anything runs, so a crash between the two loses nothing and a
replay finds the approval already spent.

**Consequences.** One path to harden, one path to audit, one place where an execution policy would
go. A client that wants the mutation now waits for a worker rather than getting it in the response —
which is honest, because the work was always going to be asynchronous the moment a queue existed,
and a synchronous endpoint that sometimes ran and sometimes queued would be worse than either.

Rejected: keeping the synchronous path behind a feature flag (a flag is a second path with extra
steps, and the flag itself becomes a bypass); making the synchronous endpoint enqueue and wait
(re-creates request-lifetime coupling to the worker and turns a queue backlog into request
timeouts).

### ADR-0049 — The provider contract is typed, and a provider has no authority

**Context.** Checkpoint 8 defined `LLMProvider` as one method returning spans, which was enough for a
deterministic fake and not enough for a real model. Two things were missing and both are the kind
that get filled in badly under deadline: what a provider is allowed to *say*, and what happens when
it says something malformed.

The second matters more than it looks. A provider that returns unparseable output is not a
low-confidence answer — it is a broken contract, and treating the two the same means a provider
outage silently becomes "the AI found nothing today". Nobody investigates a quiet day.

**Decision.** The contract is typed in both directions and the provider's responsibilities stop at
the model.

*A provider may not.* Read the database, call a repository, call a domain service, reach the Tool
Gateway, mutate anything, enqueue a job or decide an authorization. `app.agent.providers` already
cannot import `app.contexts` or `app.platform` (ADR-0045) and that contract is unchanged — this
records why it is absolute rather than convenient.

*A provider must say what answered.* `model` and `model_version` are the model that *replied*, not
the one requested. Routing means those differ, and recording the request would make
`ai_interaction.model_version` a field nobody can trust for BR-AI-32's promotion metrics. A provider
that cannot report a resolved version records the canonical identifier it was given and says so; it
never invents one.

*Failures are typed.* `ProviderUnavailable`, `ProviderTimeout`, `ProviderRateLimited`,
`ProviderInvalidResponse`, `ProviderAuthenticationFailure`, `ProviderContractViolation`. An SDK or
HTTP exception never reaches the runtime, because a domain that catches `httpx.HTTPError` is a domain
that has opinions about a transport.

*A malformed response is a contract violation, never a low confidence.* `ProviderInvalidResponse` is
raised, the interaction is recorded `failed`, and no Proposal is produced. The distinction is the
whole point: one is the model being unsure, the other is the integration being broken.

*Conversation state is a reference the runtime owns.* The request may carry an opaque
`conversation_id`, and that is all it is — a token the provider may use for its own caching. The
runtime remains the owner of context, the provider stores no business state, and nothing downstream
reads a provider's memory as truth.

**Consequences.** A new provider is one file implementing one protocol, and it structurally cannot
do anything but call a model. The Anthropic adapter is written over `httpx` rather than the vendor
SDK, so the dependency surface is unchanged and the adapter is what a provider boundary should be —
a thin client. Its integration test is opt-in and off by default, because a test suite that needs a
credential is a test suite that gets skipped in the environment that should run it.

Rejected: returning a loosely typed dict (moves parsing into the runtime, where a provider's shape
would leak into orchestration); treating malformed output as low confidence (makes an outage
indistinguishable from a quiet day); letting the provider hold conversation state (makes it a source
of truth about business context, which is the thing ADR-0002 exists to prevent).

### ADR-0050 — Confidence carries its source; an unmeasured confidence is UNKNOWN

**Context.** Checkpoint 9 recorded that `HIGH = 85` and `MIN_CONFIDENCE = 60` were numbers chosen so
the fake provider's output crossed the threshold. They were bare integers with no statement of where
they came from, and BR-AI-09 — "extraction below the per-category confidence threshold produces no
Proposal" — was only as meaningful as that provenance.

The failure this invites is specific and hard to see afterwards. A provider that reports no
confidence gets a number assigned by whoever wrote the adapter; six months later a threshold is
tuned against a mixture of calibrated probabilities, heuristics and defaults, and nobody can say
which is which. The threshold then means nothing, and the rule that depends on it is decorative.

**Decision.** Confidence is never a bare number. `ConfidenceAssessment` carries three things:

* `value` — 0–100, or absent
* `source` — `provider_reported`, `heuristic`, or `unavailable`
* `band` — `HIGH`, `MEDIUM`, `LOW`, or `UNKNOWN`

**`UNKNOWN` is a first-class band, and a missing confidence never becomes anything else.** Not HIGH,
not a default, not a midpoint. An extraction whose confidence could not be assessed is one the
system has no grounds to act on, and BR-AI-09's threshold refuses it — which is the same answer it
gives a genuinely low-confidence span, arrived at honestly.

**A heuristic is labelled a heuristic.** The fake provider's scores are `heuristic` and so is any
adapter that derives a number from something other than the model saying one. Nothing in the system
presents a heuristic as a calibrated probability, and `ai_interaction.output_summary` records the
source alongside the counts so an evaluation can separate them later.

**The threshold lives in one place.** `ConfidencePolicy` holds the minimum band and the minimum
value; the runtime asks it rather than comparing integers. Provider-specific mapping happens in
`ConfidenceNormalizer`, outside the domain, so changing how a vendor's score becomes a band never
touches Proposal, Approval or the Tool Gateway.

**No calibration model.** This is a boundary, not a solution. Making the mapping replaceable is the
work; measuring it needs outcome data that does not exist yet, and building a calibrator against no
data would be the speculative abstraction this decision is trying to avoid.

**Consequences.** "Why did the AI not propose this" becomes answerable: the band, the source and the
policy are all recorded. A provider that reports nothing is visibly a provider that reports nothing,
rather than one that appears confident. The cost is that every adapter has to state its source
explicitly, which is a sentence of work and the sentence that makes the number mean something.

Rejected: keeping bare integers with a documented convention (a convention is what the last two
checkpoints kept finding had quietly stopped holding); defaulting missing confidence to the
threshold (indistinguishable from a real score at exactly the value that decides the outcome).

### ADR-0051 — The execution deadline is derived from the approval, not stored

**Context.** BR-AI-22 gives an ApprovalRecord an execution window — default 24 hours — after which it
expires and must be re-approved. Checkpoint 9 recorded that nothing enforced it: a job that failed
repeatedly reached `dead` and its approval stayed `pending` forever.

The obvious implementation is a column. It is the wrong one here. `ApprovalRecord` is immutable
except for the execution outcome (migration 0010), and adding a deadline as stored state invites
exactly the bug the immutability exists to prevent — a value that can be edited between a worker's
retries, so the same approval has a different deadline depending on when it is asked.

**Decision.** The deadline is `decided_at + EXECUTION_WINDOW`, computed where it is needed.
`decided_at` is already frozen by the trigger, so the deadline is a pure function of an immutable
field and one constant: the same ApprovalRecord always has the same deadline, in every process, on
every retry, forever. No migration, no backfill, no second source of truth.

**Enforced inside the claim, not before it.** The expiry predicate goes into the conditional UPDATE
that already moves an approval out of `pending`:

```sql
UPDATE approval_record SET version = version + 1
WHERE id = :id AND execution_status = 'pending'
  AND decided_at + :window > now()
```

Check-then-execute has a window between the two; this has none. A worker that reads an unexpired
approval and is descheduled past the deadline fails the claim when it resumes, because the claim is
where the decision is made. Duplicate delivery, retry and two workers racing all reduce to the same
statement, and there is no arrangement of them that executes after the deadline.

**Expiry is terminal, not retryable.** A job whose approval has expired goes to `dead` immediately
rather than back to `pending`. Retrying would burn attempts against a state that cannot improve, and
the queue would look busy while nothing could ever happen.

**Consequences.** BR-AI-22 becomes a property of the data rather than a sweep that has to run. There
is no expiry job to schedule, nothing to fall behind, and an approval's status is correct even if
no worker has looked at it. `execution_expires_at` is published on the API as a derived read-only
field, so a client can show the deadline without a second source of truth existing.

The cost is that changing `EXECUTION_WINDOW` retroactively changes the deadline of every
already-decided approval. That is the correct behaviour for a policy — shortening the window should
expire things — and it is worth stating, because a stored column would have frozen the old policy
into rows nobody would think to look at.

Rejected: an `approval_expires_at` column (mutable state duplicating a derivable value, and the
immutability trigger would have to be relaxed to backfill it); a periodic expiry sweep (correctness
that depends on a job running is correctness that pages somebody at 3am); checking expiry at enqueue
only (the gap between queueing and running is exactly where the deadline passes).

### ADR-0052 — An agent produces ToolIntents; WorkOS decides what becomes a Proposal

**Context.** Until now `AgentRuntime` was both the thing that reasons and the thing that decides
which tool to call. It mapped a span kind to a tool name from a dict it owned, built the arguments
itself, and called `raise_proposal`. That was acceptable while the only agent was code WorkOS wrote,
and it leaves nowhere to stand the moment an agent is not.

Three things were wrong with it, and only the third is about external agents.

*A control that exists and is never called.* `assert_within_agent_authority` holds BR-AI-08 and
BR-AI-23 — an agent may never touch membership, roles or approval — and nothing in the system
invoked it. ADR-0047 described it as "applied last and not policy-configurable"; it was applied
nowhere. Today that is harmless because no forbidden tool is registered, which is exactly how such a
gap survives: the thing it guards has not been built yet.

*Model output reaching an argument unchecked.* `span.attributes` could supply
`committed_by_person_id`, and when it did not, the runtime defaulted to the *delegating human* — so
a commitment extracted from a message could be attributed to whoever happened to run the analysis.
BR-AI-34 forbids guessing a Person; defaulting to the analyst is worse than guessing, because it is
systematically wrong in a direction that looks plausible.

*No boundary an untrusted agent could be put behind.* There was no typed thing to validate, because
the agent constructed the Proposal directly.

**Decision.** An agent produces a `ToolIntent` and nothing else. An intent is a *request to consider*
a tool call: the tool name, the arguments, the evidence it rests on, and the confidence behind it. It
is not an execution, not a Proposal, and not a decision. `IntentValidator` — inside WorkOS, in the
Intelligence context, unreachable from `app/agent` — is the only thing that turns one into a
Proposal, and it refuses on five independent grounds:

1. the tool is not in the registry (ADR-0042);
2. the agent's capability ∩ the organization's policy does not reach it (ADR-0047);
3. `assert_within_agent_authority` refuses the action or resource outright;
4. an argument is not on that tool's allow-list, or a Person reference is not a resolved
   participant of the source Event (BR-AI-34);
5. the confidence does not meet the policy (BR-AI-09, ADR-0050).

**Person references are allow-listed against the Event, not validated for existence.** A person id
that exists in the organization is not evidence that *this* person made *this* promise. The only
Person an agent may name is one already resolved as a participant of the Event it is reading — and
when none is, the correct outcome is no Proposal, recorded as an unresolved attribution. A promise
the system attributes to the wrong colleague is worse than a promise it failed to notice.

**The tool loop stays WorkOS-controlled.** The agent says what it thinks should happen; WorkOS
decides whether that is expressible, permitted and evidenced, and a human decides whether it should
occur. An external agent enters at exactly this boundary with no additional trust: it can produce
intents, and intents are data.

**Consequences.** Every control now sits on one path with a typed input, and
`test_every_agent_control_is_invoked` asserts they are *called* rather than merely present — the
regression that produced this ADR. The agent layer loses the ability to name a tool the validator
does not accept, which is the point.

The cost is a hop: an agent that wants to do something must express it as an intent, and adding a
capability means teaching the validator about it. That is the intended friction. Rejected: keeping
the runtime's direct path and adding checks inside it (the checks would live in the component they
constrain, which is what went wrong); validating only at Proposal creation (too late to refuse an
argument the agent should never have been able to supply, and it would put agent-specific rules into
a service humans also use).

### ADR-0053 — OpenClaw: spike further, do not integrate

**Context.** OpenClaw has been named since Decision Pack v1.0 in two roles: the WhatsApp channel
adapter and the capability runtime. ADR-0027 required them to be two ports with two identities and
recorded that **"if OpenClaw cannot present two distinct identities, that is a reportable conflict,
not something to work around."** The architecture's own risk register, A-3, says the contract is
still unspecified and calls for a spike before Phase 3.

That was written before this system had a working agent runtime. It now has one, which changes what
adopting OpenClaw would be *for*, and makes the question answerable on evidence rather than on plan.

**What this repository knows about OpenClaw: nothing verifiable.** There is no API documentation, no
SDK, no request or response shape, no statement of its authentication model. Everything recorded is
intent about where it would sit, not fact about what it does.

**Decision. SPIKE FURTHER.** Not adopt, because there is nothing to adopt against. Not reject,
because nothing establishes it is unsuitable. Writing an adapter now would mean inventing the API it
adapts, and a seam shaped by imagination rather than by the thing it has to fit is worse than no
seam — it looks like progress and constrains the real integration.

**What the spike must answer, before ADOPT or REJECT is possible:**

1. **Two identities, or one?** ADR-0027 requires the channel adapter and the capability runtime to
   authenticate separately. If OpenClaw presents one identity for both, a compromised WhatsApp
   channel reaches tool permissions, and the answer is REJECT for the runtime role regardless of
   everything else.
2. **Who owns the tool loop?** This system requires WorkOS to decide which tools exist and when they
   run (ADR-0042, ADR-0052). A runtime that insists on driving its own loop can only be used as a
   producer of intents, never as an executor. That is compatible; it is also much less than
   "capability runtime" implies.
3. **Can it run with no database access?** ADR-0002 and ADR-0045 give the agent layer none. A runtime
   that expects to read business state directly is not deployable here.
4. **Does it return verifiable spans?** BR-E-05 needs character offsets into the source text. A
   runtime that returns only summaries cannot produce Evidence this system will accept.
5. **What does it report about the model that answered?** ADR-0049 requires the resolved model, not
   the requested one.
6. **How is session state held?** `conversation_id` is opaque here and the runtime owns context
   (ADR-0049). A component that becomes a source of truth about business context breaks that.

**Comparison, on what is known today.**

| | Native AgentRuntime | General agent framework | OpenClaw |
|---|---|---|---|
| Architecture fit | Exact — built to these boundaries | Usually owns the tool loop; adaptable at cost | Unknown |
| Security | Enforced by import contracts and tests | Framework-dependent; typically broad | Unknown; A-3 flags the two-role blur |
| Tool execution | WorkOS registry only | Framework registry, usually open | Unknown |
| Multi-turn | Opaque `conversation_id`, runtime owns context | Native, often stateful | Unknown |
| Provenance | Event → Evidence → Interaction → Proposal → Approval | Bolt-on | Unknown |
| Reliability | One process, one queue | More moving parts | Unknown |
| Operational cost | None beyond what exists | A dependency and its upgrades | A service, a network boundary, credentials |
| Value over current | — | Multi-step reasoning we do not need at Level 1 | Unknown; the *channel* role has clear value |

**Consequences.** CP11 integrates nothing. The `ToolIntent` boundary from ADR-0052 is what an
external runtime would enter through if the spike succeeds, and it was worth building for reasons
that have nothing to do with OpenClaw. `test_openclaw_is_not_on_any_path` keeps the name out of
`app/`.

Worth separating for whoever runs the spike: OpenClaw's **channel adapter** role has obvious value
and a narrow blast radius — it ingests untrusted text through an ingestion-only identity (ADR-0027)
and holds no tool permissions. Its **capability runtime** role is the contested one. The two can be
decided independently and probably should be.


### ADR-0054 — External identity resolution is lookup-only, and attribution is threshold-gated

**Context.** PQ-7 — "External identity resolution from phone numbers to People" — has been open since
Decision Pack v1.0. Until now nothing resolved anything: `EventParticipant.person_id` was populated
only when a caller supplied it by hand, and no code mapped a channel identifier to a Person.

That made one half of the product unreachable. A commitment needs a committer (BR-C-01), BR-AI-34
forbids the agent from naming one, and ADR-0052 allows only a participant the Event *already*
resolved. A realistic channel message carries a phone number and nothing else, so every commitment
either degraded to Work — because no participant held the speaker role — or was refused, because the
speaker held no `person_id`. The vertical the product is named for could not run.

`identity.may_attribute` (BR-I-06) was written for exactly this decision, has been unit-tested since
Checkpoint 5, and had never been called. That is the same defect class as the ADR-0052 regression: a
control that exists and is not on any path.

**Decision.** Resolution happens deterministically, in the Signal context, at Event capture, and is
**lookup-only**:

- it reads `external_identity` by `(org_id, source_system, external_id)` — the tuple that is already
  unique — and by nothing else;
- it **never creates** an `ExternalIdentity` and **never creates** a Person;
- it **never infers** from display names, name similarity, or any fuzzy match;
- the **LLM never sees or selects `person_id`**. Resolution completes before any analysis runs, and
  the agent continues to receive opaque `participant_id`s only (ADR-0052).

Attribution is permitted **only** when `confirmed_at IS NOT NULL AND confidence >= 90`
(`MIN_ATTRIBUTION_CONFIDENCE`), asked through `identity.may_attribute`. When it is not permitted:

- `external_handle` is preserved exactly as captured;
- `person_id` stays `NULL` and `resolved_at` stays `NULL`;
- the existing conservative behaviour applies unchanged — an unresolved speaker means the commitment
  intent is refused (BR-AI-34), and no speaker at all means it degrades to Work.

**Why a threshold and a human confirmation rather than one or the other.** Confidence alone is a
number some importer chose; `confirmed_at` alone says a human looked but not how sure they were. The
conjunction means a mapping can only attribute a promise to a colleague after a person in the
organization has vouched for it *and* the mapping is strong. A weak or unvouched mapping remains a
legitimate row that may suggest — it simply may not decide, which is what BR-I-06 always said.

**Consequences.** The commitment vertical becomes reachable without a migration, a new table, a new
tool or a new intent kind: every column this uses already exists. `may_attribute` moves onto a live
path, and `test_may_attribute_is_invoked_on_the_resolution_path` asserts it is *called*, not merely
correct — the ADR-0052 discipline applied to a second control.

The new risk is attribution itself: a message can now cause a Commitment to be recorded against a
real person. The blast radius is bounded by the conjunction above, by the fact that resolution reads
and never writes identities, and by the unchanged requirement that a human approve the Proposal
before anything is written. The residual risk is a spoofed channel identifier presented against an
already-confirmed mapping; that trust decision is made once, out of band, by the person who confirmed
the mapping, and not per message.

Rejected: resolving by `handle` as well as `external_id` (`handle` carries no uniqueness constraint,
so two people could match one participant and the tie-break would be invented); creating an
`ExternalIdentity` on first sight of an unknown number (self-confirming provenance — the system would
be vouching for a mapping nobody checked); letting the agent propose the mapping (BR-AI-34 exists to
prevent precisely that).


### ADR-0055 — The model quotes a deadline phrase; WorkOS reads it against the Event

**Context.** Commitments were being created with no deadline. `due_date` and `due_precision` were on
the agent's argument allow-list, the Tool Gateway accepted them, the Commitment table had the
columns — and nothing ever populated any of it, because the extraction schema never asked for a
date and the runtime never produced one. A promise with no date is invisible to BR-C-06, which is
most of what a commitment is *for*.

The obvious fix is to ask the model for a date. It is also the wrong one. "By Friday" is only a date
relative to a day the model does not know: it has no reliable clock, no idea when the message was
sent, and no knowledge of the organization's calendar — and it will produce a confident date anyway.
A date computed from the wrong "today" is worse than no date, because it is indistinguishable from a
date somebody agreed to.

**Decision.** The model **quotes**; WorkOS **reads the quote**.

- The span schema gains an optional `due_phrase`: the words in the message that name the deadline,
  copied verbatim, or null. Additive — `ExtractedSpan.attributes` already existed for exactly this,
  and a provider answering the older schema omits the key and is unaffected.
- `IntentValidator` checks the quote appears **inside the span the intent cited** — BR-E-05's
  discipline applied to the deadline. A quote from elsewhere in the message has not shown that
  *this* promise carries *that* deadline.
- `commitment.read_due_phrase` — pure, closed, in the context that owns `DuePrecision` — turns the
  phrase into a date against the Event's **`occurred_at`** and the **organization's timezone**.
- The agent's allow-list loses `due_date` and `due_precision` entirely. It may quote; it may not
  compute. `CREATE_WORK` loses `due_date` on the same grounds: nothing populated it either.

**What is read, and what is declined.** Only a calendar date, "today" and "tomorrow" are `exact`. A
named weekday is `week` — "by Friday" is a week's promise with a Friday in it, which is the reading
the domain has always described, and understating precision costs nothing because `week` is still
auto-missable. "Next week" and "end of the month" are bounded at `week` and `month`. **Everything
else returns no date at all**: "before the meeting", "soon", "when finance signs off". A phrase
longer than 40 characters is refused outright — that is where negation hides ("I will *not* manage
this by Friday" contains "by Friday") — and a date earlier than the day the promise was made is
refused as far likelier a quotation than a deadline.

**Consequences.** The same Event yields the same date on every run, in every process, forever:
re-analysing a three-day-old message cannot acquire a newer deadline, because the anchor is a column
and not a clock. The model's one job here is quoting, which is what models are reliably good at, and
the arithmetic is done by code that can be unit-tested against a fixed calendar — 33 cases, most of
them about declining.

The cost is recall: a deadline phrased outside the table produces a `vague` promise. That is the
intended direction. Rejected: asking the model for an ISO date (it would answer, from the wrong
today); passing "today" into the prompt (the model would still do the arithmetic, and unverifiably);
a natural-language date parser (a large dependency whose failure mode is a confident wrong date,
which is the failure mode being designed out).

### ADR-0056 — Provenance is a walk, not a denormalised column

**Context.** "Why does WorkOS believe this commitment exists?" has to be answerable from the
Commitment. It was not: given a Commitment id there was no route back to the Proposal that produced
it, so the chain was traversable forwards and not backwards, which BR-PR-08 requires in both
directions.

Two shortcuts suggest themselves and both are wrong. Adding `ai_interaction_id` and `evidence_ids`
columns to Commitment denormalises a relationship that already exists and creates a second truth
about it. Writing the created entity back onto `proposal.target_id` would mean editing a row the
immutability trigger freezes on purpose — `target_id` is part of *what was proposed*, and a CREATE
Proposal targets nothing because nothing existed when it was raised.

**Decision. The canonical chain is this, and it is walked rather than copied:**

```
Commitment ──(resulting_entity_id)── ApprovalRecord ──(proposal_id)── Proposal
                                            │                             │
                                      approver, hash                 evidence_ids
                                                                          │
                                                                      Evidence ──(event_id)── Event
                                                                          │
                                                                 (produced_by_id) AIInteraction
```

`ApprovalRecord` is the hinge, and it is the right one: it is the only row in the chain that
execution is permitted to write (`resulting_entity_id` is on its mutable allow-list, `proposal.*`
is not), and it is the row that records the human act the whole chain exists to evidence.

The one thing CP15 adds is the filter that makes the first hop expressible:
`GET /api/v1/proposals?resulting_entity_id=…`, one optional query parameter on an existing endpoint,
scoped on both sides of the join. It serves Work and Commitment identically, which is why it is a
filter rather than a bespoke `/commitments/{id}/provenance`.

**Consequences.** Every hop is an endpoint that already existed, authorized by the decision that
already governed it, and no column was added to hold a copy of something derivable.

**`source` is not where the AI is recorded, and that is the same decision.** Work created from an
approved Proposal carries `source = human`. `validate_work_creation` refuses `Source.AI` outright —
AI-originated work exists as a Proposal until somebody approves it, and on approval the mutation is
*the approver's act* (BR-AI-19), attributed to them. So `source` answers "was this typed, imported,
or machine-generated without review", and the AI's involvement is answered by the chain above. It
reads like a defect and is not one; `test_the_source_column_says_a_person_made_it_and_that_is_deliberate`
exists so nobody "corrects" it into a second, contradictory record of the same fact.

**A known artefact, deliberately left.** Evidence is created during analysis, before the entity it
justifies exists, so `evidence.target_id` holds a placeholder UUID that points at nothing. Evidence
is immutable except `superseded_by_id` (BR-E-06, trigger-enforced), so retargeting it after
execution would mean widening an immutability rule to fix a field nothing reads — the chain above
runs through `proposal.evidence_ids`, not through `evidence.target_id`. Recorded here so the next
reader knows it is a decision rather than an oversight. Making `target_id` nullable is the honest
long-term fix and is a migration that changes a NOT NULL on a table with an immutability trigger;
it belongs to whichever checkpoint has a second reason to touch that table.


### ADR-0057 — The policy surface is derived from the tools, and a cell's action decides

**Context.** `agent_capability_policy` stores `(capability, entity_type, action) -> mode`, and
CP19A set out to give an administrator a screen for it. Building the screen exposed two things about
the table it edits.

`PUT /agent-policy` accepted **any** `entity_type` string and any `Action`. An administrator could
store `(extract, unicorn, reassign) = level_2`, and nothing would reject it — the row simply grants
nothing, because `enabled_tools` finds no tool behind it. A row that reads like policy and decides
nothing is the worst of both: somebody believes they made a decision and nothing changed.

And `enabled_tools` consulted `(capability, entity_type)` and **ignored `cell.action` entirely**. A
cell decided about *creating* work also turned on every other tool touching work. Nothing widened in
practice — `EXTRACT` holds no state-changing tool, so the intersection came out right by luck — but
the policy's most specific field was not consulted at the point that decides.

**Decision.**

1. **One table relates policy to tools**: `TOOLS_FOR_CELL: (entity_type, Action) -> tools`,
   replacing the entity-only map. `enabled_tools` intersects the agent's capability tools with the
   tools for the cell's **entity type *and* action**. Strictly narrowing; nothing that was denied
   becomes allowed.
2. **The decidable surface is derived, not written down**: `policy_surface()` is every
   `(capability, entity_type, action)` whose tool intersection is non-empty. A capability with no
   tools — `link_evidence` today — produces no cells, so an administrator is never offered a switch
   with nothing behind it.
3. **A write outside the surface is refused** (BR-AI-30, 422), in `policy.set_mode` rather than in
   the router, so no other caller can skip it.
4. **The read publishes the grid**: `GET /agent-policy` keeps `items` (decided rows only) and gains
   `available` — every decidable cell with its effective mode, its tools, and a `decided` flag.

**`decided` is the field worth arguing about.** An absent cell is `off`, and so is a cell somebody
reviewed and switched off. Those are different facts about an organization — one of them means the
safety review happened — and a screen that rendered both as "off" would erase the difference at
exactly the moment it matters (BR-AI-32's promotion process).

**Consequences.** The editable surface has one source, and it is the code that enforces it, so the
screen cannot drift into offering something the system cannot do. Deny-by-default is untouched: the
surface carries no mode of its own, a new organization still has no rows, and every cell still
starts off. `AutonomyMode` still stops at level 2 — not disabled, absent from the type.

Rejected: validating the surface in the router (a second caller would skip it); a `policy_surface`
table in the database (a migration to store something the code already knows, which could then
disagree with it); rendering every combination as a row in `items` (the existing docstring is right
— it would bury the decided ones).


### ADR-0058 — Ingestion has no in-process port; the connector boundary is the capture API

**Context.** CP19B set out to design the abstraction for
`external message → Event → participants → identity resolution → AI analysis`. Every stage of that
already exists and is tested: `POST /api/v1/events` captures, BR-E-02 decides new-versus-duplicate
from `(source_system, source_ref)`, ADR-0054 resolves participants, and
`POST /api/v1/events/{id}/analyze` runs the agent.

So the real question was not what to build but **where the seam is**, and whether a connector port
belongs in the codebase at all.

**Decision. There is no in-process connector port, and no `ConnectorPort` protocol is introduced.**
The boundary is the HTTP capture API a connector already has.

A connector is a separate process that speaks a vendor's protocol and speaks HTTP to WorkOS. Giving
it an in-process interface would mean either importing vendor code into this repository — which
ADR-0027 put behind a separate identity precisely to avoid — or shipping a protocol with no
implementation, which is a shape nobody has checked (the CP12 finding, repeated).

**What a connector is responsible for.** Receiving and normalising, and nothing else:

1. map the vendor payload to `POST /api/v1/events`, with `type=EXTERNAL_MESSAGE`,
   `origin=external`, the vendor's own message identifier as `source_ref` and a stable
   `source_system`;
2. put each sender and recipient in `participants` as a **bare `external_handle`** with a role —
   never a `person_id`, which it has no way to know and no authority to assert;
3. send an `Idempotency-Key` **derived deterministically from `(source_system, source_ref, body)`**;
4. authenticate as an ingestion-only identity (ADR-0027), which holds no tool permissions;
5. stop. It calls no analysis, makes no judgement about content, and never calls the Tool Gateway.

**What the platform is responsible for**, unchanged and untouched by this ADR: duplicate and
revision detection (BR-E-02), identity resolution and attribution (ADR-0054, BR-I-06), extraction
eligibility (BR-E-11), the whole agent path, and every authorization decision.

**Why the key is derived, and why it includes the body.** Every message transport redelivers — that
is what an unacknowledged message *means* — so a connector sends the same message twice as a matter
of normal operation. A random key per attempt would make each redelivery a new action, which is the
failure the header exists to prevent.

Deriving it from `(source_system, source_ref)` alone fails the other way, and CP19B's conformance
suite is what found it: an **edited** message legitimately carries the original's reference, so the
guard would refuse it as "the same key with a different body" and a correction could never be
captured. It is a revision (BR-E-02), not a retry. The key therefore covers the content as well —
same bytes, same key, clean replay; different bytes under the same reference, a different action
that the platform classifies.

**Two independent guarantees, not one.** The derived key gives a *clean* redelivery: the connector
receives the original response rather than an error. Underneath it, `ux_event_source_ref` — a
partial unique index on `(org_id, source_system, source_ref)` over rows that are originals and not
deleted, added with the Signal schema in migration 0009 — is what makes BR-E-02 true regardless of
whether a caller kept the contract. Two concurrent deliveries that both skipped the header race
there, and one of them loses. The index is partial precisely so a revision may share its original's
reference.

That pairing is why CP19B needed no migration and no new mechanism: the guarantee was already at the
right level, and what was missing was a written contract and something that executes it.

**Consequences.** The contract is executable: `tests/integration/test_ingestion_contract.py` is the
conformance suite, and it is the artefact a connector author works against. Writing it found the
key-derivation defect above, which is the argument for an executed contract over a described one.

Rejected: a `ConnectorPort` protocol with no implementation (an unchecked shape — the CP12 finding);
an ingestion worker consuming a queue directly (a second write path into Signal, and the one that
would bypass the idempotency guard); a `connectors/` package (nothing would go in it).

### ADR-0059 — OpenClaw publishes no outbound delivery contract; the integration direction reverses

**Context.** ADR-0053 recorded **SPIKE FURTHER** on OpenClaw and said, accurately at the time, that
"what this repository knows about OpenClaw: nothing verifiable". CP19B was asked to investigate
properly rather than restate that.

**What the spike found.** OpenClaw's own channel documentation describes WhatsApp as an **inbound
channel inside OpenClaw's gateway runtime**: a CLI (`openclaw channels login`, `openclaw gateway`),
JSON5 configuration for access policy and routing, and Baileys session credentials on disk per
account. Messages are handled *within* that runtime.

It documents **no outbound delivery mechanism**: no webhook, no HTTP callback, no event payload
schema for external consumption, no WebSocket event contract intended for a third-party system.
There is therefore nothing for a WorkOS-side adapter to be written against.

On ADR-0027's specific question — can OpenClaw present two distinct identities, one for the channel
adapter and one for the capability runtime — the separation that exists is *internal*: per-account
credential directories inside one gateway. That is not the external adapter-to-system credential
boundary ADR-0027 required, and ADR-0027 said that would be "a reportable conflict, not something to
work around". This is that report.

**Decision. No OpenClaw code enters this repository, and the direction of integration reverses.**

The only shape specifiable today is **OpenClaw as an HTTP client of WorkOS**: something running
beside the gateway calls `POST /api/v1/events` under the contract in ADR-0058. That requires no
OpenClaw API, no adapter, and no assumption about a payload nobody has published — and WorkOS needs
no code for it, because the contract is the API it already serves.

**Consequences.** ADR-0053's spike is closed on its capability-runtime question and on its
channel-adapter question, with evidence rather than with an absence. `test_openclaw_is_not_on_any_path`
continues to keep the name out of `app/`. Nothing in this ADR licenses writing an adapter; if
OpenClaw later publishes an outbound contract, ADR-0058's boundary is where it would arrive, and the
decision to adopt it would be a new record.

Sources consulted: `docs.openclaw.ai/channels/whatsapp` (fetched 2026-09-13). Several other domains
present themselves as OpenClaw documentation; none was treated as authoritative, and no field name,
endpoint or schema from any of them appears in this repository.


### ADR-0060 — An ingestion-only role, holding one grant

**Context.** ADR-0027 required a channel connector to authenticate as an ingestion-only identity
holding no tool permissions, and nothing ever issued one. A connector would therefore have run as
`member` — the narrowest existing role holding `EVENT.CREATE`.

`member` holds **56 of the authorization matrix's 83 cells**, including `PROPOSAL.APPROVE`. A stolen
delivery credential could have approved the AI's own proposals: deliver a message, have the agent
read it, approve what it proposed, and write into the organization with no person anywhere in the
chain. That is the one control the Level-2 autonomy model rests on, reachable from a credential
whose whole job is to receive email.

**Decision. `Role.INGESTION`, holding exactly one grant: `EVENT.CREATE`.**

It authenticates the same way everything else does — a Keycloak subject resolved to a Person and
their roles (ADR-0031). A connector is not a human, but a second authentication path would be a
second way into the application and a second thing to get right.

**The reads are denied too, and that is deliberate.** A connector needs no read to do its job: the
capture response tells it what happened. Denying them means a stolen delivery credential cannot be
used to page through an organization's messages or work, which is the thing it would otherwise be
most useful for. It cannot even read back the Event it just delivered.

**Two consequences of that worth knowing**, both verified rather than assumed:

* **Analysis is refused as 404, not 403.** `POST /events/{id}/analyze` reads the Event through the
  caller's own visibility (BR-E-08), and a connector cannot see Events at all. The refusal comes
  from the narrowing rather than from a separate check, which is the stronger arrangement — there is
  no permission to grant by accident later.
* **A redelivery *without* the idempotency key answers 404.** Telling a caller "you already sent
  this, here it is" means returning the Event, which this role may not read. No second Event is
  written either way; what the connector loses is a usable answer. The derived key avoids the read
  entirely, because the idempotency guard replays a stored response without going near the row.

**Listing endpoints answer 200 with nothing rather than 403.** They narrow by the grants a role
holds instead of refusing up front, so a role with none gets an empty page. The confidentiality
property is identical and is what the tests assert against a populated organization.

**`row()` defaults the new column to denial.** This file's usual discipline is that adding a role
breaks the build until every cell is filled in deliberately. `ingestion` is *defined* as denied
everywhere but one cell, so a default of `NO` states exactly that, and
`test_the_ingestion_role_holds_exactly_one_grant` replaces the build break with a stronger
assertion: it checks the whole matrix rather than checking that somebody typed something.

**Consequences.** Migration `0014` widens one CHECK constraint's value list — no column, no data, no
RLS, no change to any existing role. Its `downgrade` refuses while `ingestion` assignments exist
rather than deleting them: revoking access is a decision with an actor and an audit entry, not a
side effect of a schema rollback.

Rejected: running as `member` (56 cells for a job that needs one); a service-account authentication
path outside Keycloak (a second way in); granting `EVENT.READ` so redelivery could answer cleanly
(it trades the confidentiality property for a status code).

### ADR-0061 — Email over IMAP is the first connector; PQ-1 amended

**Context.** Decision Pack v1.0 PQ-1 named three capture sources — the web paste path, OpenClaw
messaging with WhatsApp first, and internal system-generated Events — and listed email among its
non-goals. ADR-0059 then established that OpenClaw publishes no outbound delivery mechanism, so the
only *external message* source PQ-1 sanctioned could not be built. The internal source is
explicitly not an external capture source and is non-extractable by BR-E-11.

The pack could therefore not be satisfied: its sanctioned connector was un-buildable and its
buildable connectors were non-goals. That was reported as a conflict rather than resolved in code,
and the Product Owner amended PQ-1.

**Decision (Product Owner, recorded here). PQ-1 is amended: email over IMAP is permitted as the
first external connector.** OpenClaw/WhatsApp remains a sanctioned future source and is not to be
integrated until a verified external interface exists.

**Why email, on the criteria that were asked for.** IMAP is RFC-specified and vendor-neutral, so
there is a contract to build against rather than a product's current behaviour. It runs entirely
outside WorkOS. `Message-ID` is *defined* by RFC 5322 to be globally unique, which is exactly what
`source_ref` needs — BR-E-02's key comes free rather than being invented. And it needs no
dependency: `imaplib` and `email` are in the standard library, which is the strongest available
statement that a connector shares nothing with the application it posts to.

**What the connector is, and is not.** It lives in `connectors/imap/`, outside the backend package,
and speaks only the contract in ADR-0058. The interesting half is pure — bytes in, an event body out
— which is why it is a module rather than a step inside the IMAP loop, and why most of its tests
need no mail server. Two architecture tests hold the boundary: the application imports no connector,
and the connector imports no context, session, ORM or web framework.

It refuses rather than invents. A message with no `Date` and no server receipt time is skipped, not
delivered with `now()` — CP15 reads deadlines against `occurred_at` (ADR-0055), so an invented
timestamp would become a real date on somebody's promise. Addresses become bare handles and never a
`person_id`. Quoted replies are delivered intact, because deciding a reply is not part of a message
is interpretation and belongs where it can be audited.

**Consequences.** `connectors/` is a new top-level directory; CLAUDE.md §4 requires an ADR for that
and this is it. The MVP gains a capture source that a pilot can actually use, and the adapter layer
PQ-1 asked for is demonstrated rather than asserted — a second connector is a second directory
speaking the same contract, with no change to WorkOS.

Not decided here: whether email is a source the product *wants* long-term, which is a product
question the pilot will answer. This ADR records that it is permitted and that it is first.


### ADR-0062 — A connector is an untrusted edge, confined and tested like one

**Context.** CP20 shipped the IMAP connector with its normaliser and its delivery client covered and
its actual IMAP conversation covered by nothing — 198 lines whose only verification was that they
type-checked. CP21's recommendation was to run it against a real mailbox and fix what broke. A
production mailbox was not available; a real mail server was.

**Decision, in three parts.**

**1. A connector is confined by topology, not by discipline.** ADR-0002 argues the agent runtime
must be unable to reach the database, and enforces it in `docker-compose.yml` rather than in
review. A connector is the system's *other* untrusted edge — it speaks a vendor's protocol to the
open internet — and gets the same treatment: `edge` network only, no data-tier credential in its
environment, and no defaulted password or token in the compose file.
`test_no_connector_is_attached_to_the_data_network` and its two siblings assert this file keeps that
shape, matching the agent tests that have guarded the same property since Phase 1.

**2. Its tests are layered the way the provider tests are.** `test_canonical` and `test_client` need
nothing and always run. `test_mailbox` drives the real `imaplib` client against a real IMAP server
(GreenMail, a `dev` profile in compose) and is **opt-in** — `RUN_IMAP_TESTS=1` — because a suite
that needs a service to be up is a suite that gets skipped in the environment which should run it.
That is the same argument, and the same shape, as the real-provider smoke tests.

**It found a real defect immediately**, which is the argument for the layer existing.
`imaplib.Internaldate2tuple` returns a *local-time* struct for the correct instant; the connector
was treating those fields as UTC, so a message with no `Date` header arrived with a timestamp shifted
by the connector host's own offset — seven hours on the machine it was found on. CP15 reads
deadlines against `occurred_at` (ADR-0055), so that would have become a wrong date on somebody's
promise, silently. It is now pinned by a test that needs no server, using an INTERNALDATE with a
non-zero offset so it fails on a UTC host too.

**A test server is not a real mailbox**, and this ADR does not claim otherwise. GreenMail is an
independent implementation of RFC 3501, so it catches assumptions a fake written alongside the
client would have shared; it does not catch what a particular provider does with folder names, flags
or fetch responses. That risk is closed by a pilot, not by another test.

**3. TLS is the default and clear text has to be asked for.** `IMAP_SECURITY` is `ssl` (implicit TLS
on 993), `starttls` (the 143 upgrade many servers offer, performed before `login` so the password
never crosses in clear), or `none`. `none` exists because the test server needs it, and it is the one
value with no default path to it — a mailbox password in clear text is not something to arrive at by
leaving a setting unset.

**Consequences.** The connector runs as a service (`--interval`) and stops cleanly on SIGTERM, so a
pass in flight finishes and a message is never marked seen by a process killed before WorkOS had it;
the reverse is harmless because the redelivery carries the same derived key.

Attachments are still not delivered — that is the presigned-upload flow of ADR-0039 and a piece of
work in its own right — but they are no longer dropped in silence: the loop names what it left
behind. They are deliberately absent from the Event body, because mentioning them there would put
words into a record nobody wrote and Evidence is quoted from it (BR-E-05).

Rejected: an in-process IMAP fake as the only server-side test (it would share the client's
assumptions, which is precisely what the defect above was); making the mail server a default compose
service (a test dependency in a deployment); a `--once` flag (one pass is what you get by not asking
for a service).


### ADR-0063 — Attachment delivery by a connector

**Status: accepted and implemented in CP23.** The three blockers below were put to the Product
Owner and resolved; what follows records both the problem and what was built. CP22 set out to deliver
attachments from the IMAP connector and stopped, because doing so requires two changes to security
boundaries and one to network topology. Recorded here so the decision is made rather than arrived
at.

**What CP22 did do**, because it turned out to be the precondition for any of this: it executed the
attachment flow for the first time. `S3ObjectStore` is the only module importing boto3, it issues
credential-bearing URLs, and it had **no test of any kind** — every attachment test installs
`InMemoryObjectStore`. MinIO also published no ports, so a presigned URL was unreachable from the
host by any client at all. Both are now fixed and the whole ADR-0039 sequence is verified against a
real store: the presigned PUT genuinely grants the upload, `stat` agrees with the bytes, an expired
URL is refused, a tampered key is refused, an unsigned request is refused, and an unfinished upload
leaves a `pending` row rather than one claiming a file nobody can fetch.

**The three blockers.**

**1. A connector cannot reach the attachment endpoints.** `AttachmentService.start` loads the Event
through the visibility-narrowed query — which is the line that makes "attachment authorization is
Event authorization" true rather than intended. The ingestion role holds `EVENT.CREATE` and nothing
else (ADR-0060), so the load returns nothing and the call 404s. Delivery needs `EVENT.ATTACH` *and*
some form of `EVENT.READ`.

Granting organization-wide read reverses the decision ADR-0060 exists for: that a stolen delivery
credential cannot page through an organization's messages.

**2. The narrow alternative is not a matrix edit.** "May read the Events it captured" is the right
shape — the connector delivered them, it already had the content. But `_reach_predicate` for
`(EVENT, READ)` implements only `ORG` and `DENY`; a `PERSONAL` grant falls through to *no narrowing
at all*, which is organization-wide read arrived at silently. Making that grant mean what it says is
query work with a test, not a cell change. The module's own docstring anticipates this.

**3. The connector cannot reach the object store.** A presigned URL points at MinIO, which lives on
the `data` network. The connector is on `edge` only, and attaching it to `data` is precisely what
ADR-0002 and ADR-0062 forbid. In a deployment with a public S3 endpoint the URL is reachable and
this evaporates; in this stack it does not, and "which endpoint does a client get" is currently one
setting (`s3_endpoint_url`) serving two different audiences.

**Resolution, as built.**

* **`Grant.PERSONAL` is implemented in `_reach_predicate`** as `captured_by_person_id = :me`, and
  the function's fall-through now *denies* rather than returning `None`. That ordering was not
  optional: a `PERSONAL` cell added before the predicate existed would have granted organization-wide
  read silently, which was demonstrated before the change was made.
* **Two cells**, `(EVENT, READ)` and `(EVENT, ATTACH)`, both `OWN`. The attach half needed only the
  cell: `event_relations` already answered `PERSONAL` for whoever captured the Event, so a connector
  attaching to somebody else's is refused by machinery that predates this record.
* **The object store endpoint is split.** `S3ObjectStore` holds two clients: presigned URLs are
  signed for the endpoint a client can reach, `stat` and `delete` go over the internal one. A
  presigned URL cannot be rewritten after signing — the signature covers the host — which is why
  this is two clients and not string surgery, and is asserted by a test that rewrites one and gets
  a 403.
* **MinIO stays on `data`.** A reverse proxy (`objects`) sits on `app` and `data` and is the only
  service besides the API that spans the boundary. The connector reaches objects through it and
  cannot address the store at all.

**A hole this opened, and closed.** Granting the connector `PERSONAL` read removed the only thing
that had been stopping it from calling `POST /events/{id}/analyze` — that endpoint had **no
authorization check of its own**, so its refusal had been incidental. Any authenticated principal
who could see an Event could have an analysis run under their name, including `viewer`, `auditor`
and `executive`, all of whom are denied `PROPOSAL.CREATE`. The endpoint now authorizes the caller
for exactly that, which is BR-AI-03 — an agent's authority is the intersection of its own and the
delegating human's — made real at the entry point rather than assumed.

**A limitation this removed.** ADR-0060 recorded that a redelivery without the idempotency key
answered 404, because telling a connector "you already sent this, here it is" meant returning an
Event it could not read. It can read its own now, so the fallback is the original Event rather than
a confusing refusal. The derived key is still worth sending: it replays without touching the row.

**What the connector does with it.** It carries each file's bytes — a message is whole in memory by
the time it is parsed, so pretending to stream would be ceremony — bounded by
`MAX_ATTACHMENT_BYTES`, and delivers them after the Event exists: reserve, PUT to the presigned URL,
complete. It sends no credential to the store, because the URL is the authority. A file that fails
is named and the rest continue: an Event with three of its four attachments is a better record than
no Event at all. Oversized parts are skipped rather than truncated, because half a PDF is not a
smaller PDF.

**What the Event does not say.** Attachments are absent from `body_text` and from the capture
payload. Naming them there would put words into a record nobody wrote, and Evidence is quoted from
it (BR-E-05). The AI reads the covering note; the file sits beside the Event, reachable through the
flow ADR-0039 designed.

Rejected: granting the ingestion role organization-wide `EVENT.READ` (reverses ADR-0060 for
convenience); putting the connector or the store on each other's networks (reverses ADR-0062 and
ADR-0020); accepting attachment bytes through the capture API (reverses ADR-0039, which exists
because an API process is the wrong place for file transfer); rewriting the host of a presigned URL
after signing (does not work, and the test says so).

---

### ADR-0064 — Tenant scoping is not authorization

**Status:** accepted (CP24) · **Supersedes nothing** · **Related:** ADR-0008, ADR-0027, ADR-0060

**Context.** Every read path in this system narrows rows to the caller's organization, and for the
whole of Phase 1 that was indistinguishable from authorization: every role in the matrix held an
`ORG` grant on everything it could read at all, so "scope to the tenant" and "consult the matrix"
returned the same rows for every query anybody wrote. Several read paths therefore never consulted
the matrix. `app/contexts/commitment/queries.py` said so in its own module docstring, as a decision
rather than an omission — and it was correct, at the time it was written.

ADR-0060 added `ingestion`, the first role for which the two questions differ. It holds three cells
out of eighty-three. CP24 asked the running system what a connector's token could actually read.

**Measured, against the pilot stack.** A connector token could read every Proposal in the
organization with its full action and arguments, every Commitment, every AI interaction, the
organization's autonomy policy, and any Evidence row by id. The matrix denied all of it. Nothing
consulted the matrix.

Two things made it invisible. The first is the equivalence above. The second is that the test which
existed to catch exactly this — `test_a_connector_reads_no_business_data` — asserted `items == []`
against a fixture that created one Work and nothing else, so for `/proposals` and `/commitments` it
was asserting that an empty table is empty.

**Decision.**

1. **A read path authorizes, or narrows, and says which.** Where a role's grant is genuinely
   narrower than the tenant — `EVENT.READ = PERSONAL` for a connector — it stays a predicate in the
   query, because that is the only place it can be one. Where every role that holds the grant holds
   it organization-wide, the handler gates on the matrix before selecting a row
   (`app.platform.http.deps.may_reach`). Scoping to `org_id` is neither of these and never stands
   in for either.

2. **A published cell means something.** `EVENT.LIST` is denied to `ingestion`, and CP23 left the
   listing answering on the grounds that the reach predicate — derived from `EVENT.READ` — made the
   page uninteresting anyway. That reasoning was sound about that pair and unsound as a habit: it
   is what left the rest of the surface open. The cell is now enforced. This changes nothing for
   any human role; all seven hold `ORG` on it.

3. **An empty answer is not evidence of a control.** A test that asserts emptiness must first make
   the endpoint non-empty for somebody, or it is asserting nothing. The boundary tests now populate
   a row of every listed type, assert an administrator sees it, and assert the connector does not.

**Consequences.** `ApprovalRecord` and `AIInteraction` have no resource type of their own in the
matrix and are gated on `PROPOSAL`, which is what they are the provenance of: reading who approved
what, or which prompt and model produced a Proposal, is reading that Proposal's history.

Rejected: adding matrix rows for `ApprovalRecord` and `AIInteraction` (new policy surface, and the
grant would be identical to the Proposal's in every cell); re-deriving the narrowing inside each
query module (there is nothing to narrow — the answer is all of the tenant or none of it); leaving
it and documenting the limitation (ADR-0060 promised the opposite in writing, and CP20 made
verifying it an explicit condition of accepting the role).

---

### ADR-0065 — The connector's per-message ceiling, measured

**Status:** accepted (CP24) · **Amends:** ADR-0063 · **Related:** ADR-0058, ADR-0061

**Context.** `MAX_ATTACHMENT_BYTES = 10 MB` was introduced in CP22 with the justification that "a
message is whole in memory by the time it is parsed, so this is what keeps that bounded". CP24 was
asked to justify the number from measurement or change it.

**Measured, in the deployed connector image, one child process per size so each peak is its own:**

| attachment | on the wire | peak RSS | parse |
| --- | --- | --- | --- |
| 1 MB | 1.35 MB | 26 MB | 0.02 s |
| 5 MB | 6.75 MB | 73 MB | 0.12 s |
| 10 MB | 13.51 MB | 132 MB | 0.23 s |
| 25 MB | 33.77 MB | 313 MB | 0.56 s |
| 64 MB | 86.46 MB | 773 MB | 1.49 s |

Peak memory is close to twelve times the file: base64 puts 1.35× on the wire, and the raw document,
the decoded payload and the `Attachment` copy are live at once. So 10 MB is a ~130 MB process, and
that is the number the limit was actually choosing.

**And the claim it was justified by was false.** The same measurement with ten 10 MB files in one
message — every file inside the per-file ceiling — peaked at **1.13 GB**. A per-file limit bounds
nothing about a message.

**Decision.** `MAX_ATTACHMENT_BYTES` stays at 10 MB, now on the evidence above: it covers the
quotes, decks and scanned invoices the connector exists for, at a cost per message that a small
container can hold. A second limit, `MAX_MESSAGE_BYTES = 32 MB`, is checked on the wire **before
parsing**, which is where the amplification happens. 32 MB on the wire is roughly 24 MB of content
and a ~350 MB peak, and is above what the large providers will deliver (Gmail and Outlook both stop
around 25–35 MB), so a message refused here is one a mail server would very likely have refused
first.

A message past the ceiling is marked seen and reported as refused, not left for the next pass: it
will be exactly as large next time, and the connector's existing rule for a permanent refusal is
that repeating it turns a connector's own limit into somebody else's outage.

An oversized *file* is still skipped rather than truncated — half a PDF is not a smaller PDF — but
it is now named, with its size, on the message that carried it. It was dropped silently before,
which made "the attachment never arrived" a question with no answer anywhere in the system.

Rejected: raising the per-file limit without a per-message one (leaves the real bound unstated);
streaming attachments out of the parsed message (IMAP hands over the whole document; there is
nothing to stream from); refusing the message and leaving it unseen (an infinite retry on a
condition that cannot change).

---

### ADR-0066 — The connector obtains and renews its own credential

**Status:** accepted (CP25) · **Amends:** ADR-0058, ADR-0060

**Context.** ADR-0058 said a connector is handed a bearer token by whoever deploys it. CP24 ran
that against a real mailbox and the realm's fifteen-minute access token expired mid-run. Two things
went wrong and only one was CP24's to fix: the connector read the resulting 401 as "this message is
wrong", marked it seen and moved on — a message consumed from the mailbox and never delivered — and
there was no way for it to get another token. CP24 fixed the misreading. Nothing that needs an
operator four times an hour can be dogfooded for a week, which is what CP25 exists to make possible.

**Decision.** The connector holds a **token source**, not a token.

* `ClientCredentials` performs the OAuth 2.0 client-credentials exchange (RFC 6749 §4.4) against
  the realm's token endpoint, caches the result, and re-fetches it `leeway` seconds before expiry —
  the gap has to cover the slowest request the token will be attached to, which is an attachment
  upload rather than a capture.
* `StaticToken` is a credential handed in from outside. It still works, and it says out loud that
  it cannot renew: both in a startup warning and in the error a rejection produces. A source that
  silently returned the same dead token would make an expired credential look like a broken API.
* A 401 or 403 renews **once** and retries **once**. A token that has aged out is the ordinary case
  for a service left running and should cost one round trip, not a person. A second refusal is an
  outage, and the mail stays in the mailbox.
* Obtaining a token is logged — the client id, the lifetime, the leeway. Never the token, never the
  secret. Authentication that can only be inferred from the absence of 401s is not observable.

**A prerequisite this forced.** Keycloak's `start-dev` derives `iss` from the request's Host header,
so a token minted inside the network said `http://keycloak:8080/...` while the browser's said
`http://localhost:8080/...`, and the API — which compares `iss` to a fixed string — could only
accept one. CP24 measured `KC_HOSTNAME_URL` as having no effect and worked around it by minting
every token from the host, which is exactly what CP25 needed to stop doing. `KC_HOSTNAME` *does*
take effect: it fixes the hostname while the port follows the request, so both addresses now yield
one issuer.

Rejected: lengthening the realm's token lifetime (moves the failure rather than removing it, and
makes a stolen token worth more); a refresh token (client credentials has no user to refresh on
behalf of, and RFC 6749 §4.4.3 says not to issue one); the connector reading the API's JWKS and
minting its own (that is forging tokens, whatever it is called).

---

### ADR-0067 — A presigned URL is signed for the client that will use it

**Status:** accepted (CP25) · **Amends:** ADR-0063

**Context.** ADR-0063 split the object store's address in two: the one this process uses, and the
one "a client" uses. That was right and one word too coarse. There are two kinds of client and they
are on different networks. A connector uploads from the application network and addresses the proxy
by its service name; a browser downloads from a person's laptop and cannot resolve a Docker service
name at all.

CP24 signed both for `objects:9000` and nothing noticed, because the only thing that had ever
fetched an attachment was a test running inside the network. CP25 opened one in a browser.

**Decision.** Three endpoints, one per route, each the name the holder of that URL will actually
resolve:

| Setting | Used by | Points at |
| --- | --- | --- |
| `WORKOS_S3_ENDPOINT_URL` | the API itself, for `stat` and `delete` | the store, internally |
| `WORKOS_S3_UPLOAD_ENDPOINT_URL` | a connector's presigned `PUT` | the proxy, by service name |
| `WORKOS_S3_PUBLIC_ENDPOINT_URL` | a browser's presigned `GET` | the proxy, published |

Each falls back to the one above it, so a deployment where every client reaches the store by one
name — a cloud deployment with a public bucket — behaves exactly as before.

The object proxy is published on loopback for the browser's sake. The port it is published on need
not match the port it listens on: the signature covers the `Host` header the client sends, and
nginx forwards that header unchanged, which is the same property ADR-0063 relies on.

MinIO does not move. It is still on `data`, still unreachable from `edge`, and still addressed only
through the proxy — an architecture test asserts the public address is not a Docker service name
*and* that the proxy is published on the port URLs are signed for, because either half alone
passes while the thing is broken.

Rejected: rewriting the host after signing (does not work — the signature covers it, and ADR-0063
already has a test that proves it); routing attachment bytes through the API (reverses ADR-0039);
one address resolvable from both sides via `*.localhost` (works in Chrome and Firefox, not
everywhere, and a file that opens for some colleagues is worse than one that opens for none).

---

### ADR-0068 — The organization is named by the person, in the browser

**Status:** accepted (CP25) · **Related:** ADR-0008, ADR-0031

**Context.** `app/platform/principal.py` has always said the organization "arrives in an explicit
header and is never inferred — not from a single membership, not from a default", and the SPA has
always rendered "Choose the organization you are working in." Until CP25 there was nothing to
choose with: `setOrganizationId` existed and no component called it. A real sign-in against real
Keycloak reached that sentence and stopped, having made no API call at all.

**Decision.** The identifier is typed into a field, checked once against `/api/v1/me`, and
remembered per browser only after it resolves.

Listing a person's organizations is not something this system can offer, and that is a consequence
of two deliberate choices rather than an oversight. The token carries no organization claim
(security-model §2: a claim keeps asserting a membership after it is revoked), and every table that
could answer the question is under RLS keyed on `app_current_org()`, so a session with no
organization context sees nothing anywhere. Answering it would need a claim, or a `BYPASSRLS` role,
or a policy that lets a person read rows outside any tenant — each of which reverses something
ADR-0008 or ADR-0031 decided on purpose, to make a picker more convenient.

Checking before storing is the part that is not optional. An identifier that is not yours 404s
every subsequent request, and an app that looks empty is far harder to diagnose than one that says
"no organization with that identifier has you as an active member". A member holding no role gets a
different sentence, because it is a different problem with a different fix.

One consequence in the API client: a header set explicitly by a caller now wins over the configured
one. Without that, the check validates whichever organization is already selected rather than the
one being tried — right when nothing is selected yet, wrong the moment somebody switches.

Rejected: inferring a single membership (the rule this system states, broken for convenience, and
correct right up until the first person joins a second organization); putting memberships in the
token; a cross-tenant bootstrap endpoint behind a privileged role (a new credential with the
broadest possible read, to save typing a UUID once per browser).
