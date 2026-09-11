# System Architecture — AI WorkOS

Version 0.1 · Status: proposed

Derived from `docs/product/product-constitution.md`. Decisions referenced as ADR-nnnn are catalogued
in `docs/decisions/ADR-INDEX.md`.

---

## 1. Architectural drivers

| Driver | Consequence |
|---|---|
| AI must never modify the database | Mutation funnels through a Tool Gateway over application services; the agent runtime has no data-tier credentials and no network route to the data tier (ADR-0002) |
| System useful without AI | Work Core is a standalone deliverable; AI components are optional processes that can be stopped without degrading correctness |
| Everything must be explainable | Append-only signal store, evidence links on every inferred assertion, full audit trail (ADR-0013) |
| Minimal manual entry | Ingestion pipeline and proposal/confirmation UX are first-class, not add-ons |
| Small team, pilot-scale, Compose deployment | Modular monolith with strict internal boundaries, not microservices (ADR-0001) |
| Organizational data is sensitive | Tenant isolation, resource-scoped authorization, classification on ingested content (see `docs/security/security-model.md`) |
| MVP capture is web paste plus one messaging channel (PQ-1) | A `ChannelAdapter` port sits beside the `AgentRuntime` port; channels are ingestion clients, not Work Core dependencies (ADR-0026, ADR-0027) |
| Internal activity is also an Event (PQ-1) | The Work Core emits Events as well as DomainEvents, with explicit loop prevention (ADR-0028) |
| AI autonomy is Level 1-2 (PQ-3) | Mutations originating from AI require a recorded ApprovalRecord before execution; auto-apply exists only for deterministic internal actions (ADR-0030) |
| Multi-organization from day one (PQ-2) | `org_id` everywhere, RLS plus application scoping, shared Keycloak realm (ADR-0008, ADR-0031) |

Expected pilot scale, used to size decisions: 1 organization, hundreds of people, thousands of active
work items, order 10⁴–10⁵ events per month. Nothing here requires horizontal sharding, streaming
infrastructure or a separate search cluster at that scale.

## 2. System context

```
   ┌──────────────┐     ┌──────────────┐     ┌─────────────────┐
   │   People     │     │  WhatsApp    │     │  LLM provider   │
   │ (browser UI) │     │  via OpenClaw│     │   / models      │
   │  paste/entry │     │ channel adptr│     │                 │
   └──────┬───────┘     └──────┬───────┘     └────────┬────────┘
          │ OIDC + REST        │ ingest API           │ (egress only,
          │                    │ (connector account)  │  from agent runtime)
   ┌──────▼────────────────────▼──────────────────────▼────────┐
   │                        AI WorkOS                          │
   │                                                           │
   │   Work Core  ·  Signal Store  ·  Intelligence Layer       │
   └──────┬───────────────────┬──────────────────┬─────────────┘
          │                   │                  │
     ┌────▼────┐        ┌─────▼─────┐      ┌─────▼─────┐
     │Keycloak │        │PostgreSQL │      │  MinIO    │
     │  (IdP)  │        │  + Redis  │      │ (raw blob)│
     └─────────┘        └───────────┘      └───────────┘
```

## 3. Component architecture

```
                         ┌──────────────────────────────┐
                         │  Web App (React/TS/Vite)     │
                         └───────────────┬──────────────┘
                                         │ HTTPS, OIDC bearer
                    ┌────────────────────▼───────────────────────┐
                    │           Edge / Reverse proxy             │
                    └───┬──────────────────────────────────┬─────┘
                        │ public API                       │ /realms (Keycloak)
        ┌───────────────▼──────────────────────────────┐   │
        │          API Service (FastAPI)               │   │
        │                                              │   │
        │  ┌────────────┐        ┌──────────────────┐  │   │
        │  │ HTTP API   │        │  Tool Gateway    │◄─┼───┼──── agent runtime
        │  │ /api/v1    │        │  /internal/tools │  │   │     (internal net only)
        │  └─────┬──────┘        └────────┬─────────┘  │   │
        │        │                        │            │   │
        │   ┌────▼────────────────────────▼────────┐   │   │
        │   │      Application Services            │   │   │
        │   │  (use cases · authz · transactions   │   │   │
        │   │   · audit · outbox emission)         │   │   │
        │   └────┬─────────────────────────────────┘   │   │
        │        │                                     │   │
        │   ┌────▼──────────────┐   ┌───────────────┐  │   │
        │   │ Bounded contexts  │   │  Platform     │  │   │
        │   │ (domain + repos)  │   │  (db, authz,  │  │   │
        │   └────┬──────────────┘   │  audit, otel) │  │   │
        │        │                  └───────────────┘  │   │
        └────────┼─────────────────────────────────────┘   │
                 │                                         │
        ┌────────▼─────────┐  ┌───────────┐  ┌─────────────▼──┐
        │   PostgreSQL     │  │  Redis    │  │   Keycloak     │
        │ (system of record│  │ (cache,   │  └────────────────┘
        │  + outbox + audit│  │  streams, │
        │  + pgvector)     │  │  locks)   │
        └────────▲─────────┘  └─────▲─────┘
                 │                  │
        ┌────────┴──────────────────┴─────────┐      ┌──────────────────┐
        │        Worker Service                │      │  Agent Runtime   │
        │  · ingestion normalisation           │      │   (OpenClaw)     │
        │  · outbox dispatch                   │      │                  │
        │  · deterministic monitors (scheduled)│      │ · extraction     │
        │  · notification fan-out              │─────▶│ · summarisation  │
        │  · agent job dispatch                │ job  │ · Q&A            │
        └───────────────┬──────────────────────┘      └────────┬─────────┘
                        │                                      │
                 ┌──────▼──────┐                        tool calls (HTTP)
                 │   MinIO     │                               │
                 │ raw payloads│◄──────────────────────────────┘
                 └─────────────┘        (read of specific blobs only,
                                         via presigned URL issued by API)
```

Deployable processes: `web`, `api`, `worker`, `scheduler`, `agent-runtime`, plus `postgres`,
`redis`, `keycloak`, `minio`, `proxy`.

`api` and `worker` run the same codebase with different entry points. `agent-runtime` is a separate
image with a separate network and no data-tier credentials.

### 3.1 Why the Tool Gateway is a separate surface from the HTTP API

Both call the same application services. They differ in principal model, schema shape and controls:

- The HTTP API authenticates a human, returns resource representations, and is tuned for UI use.
- The Tool Gateway authenticates an agent acting on behalf of a principal, exposes a small
  intention-shaped vocabulary ("record_commitment", not "PATCH /commitments/{id}"), requires an
  `ai_interaction_id` and evidence references on every mutation, enforces per-interaction blast
  radius and idempotency, and rejects anything not on the tool allow-list.

Keeping them separate lets us harden the AI path without complicating the human path, and lets the
tool vocabulary evolve independently of REST resources.

## 4. Bounded contexts

Seven contexts. Each is a Python package with a published interface module; cross-context access goes
through that module, never through another context's tables (ADR-0001).

| Context | Owns | Depends on (read) | Does not |
|---|---|---|---|
| **Identity & Organization** | Organization, Department, Team, Person, Membership, Role assignment | Keycloak (upstream) | Own credentials or passwords |
| **Work Core** | Project, Milestone, Work, Dependency | Identity | Know about AI, events or notifications |
| **Commitment & Accountability** | Commitment, its lifecycle, fulfilment links | Identity, Work Core | Duplicate Work state |
| **Governance** | Risk, Decision | Identity, Work Core, Commitment | Mutate Work |
| **Signal** | Event, Evidence, source registry, raw payload refs | Identity | Interpret meaning |
| **Intelligence** | AIInteraction, proposals, prompt/tool versions, evaluations | everything (read via interfaces) | Own any work state |
| **Notification** | Notification, preferences, delivery, digests | everything (read via interfaces) | Decide domain truth |

Cross-cutting platform capabilities (not contexts): authentication, authorization policy, audit log,
outbox, transactions, telemetry, configuration, storage access.

Context relationships in DDD terms:

- Work Core is the **upstream core domain**. Commitment, Governance and Notification are downstream
  conformists to its published language.
- Signal is a **generic supporting** context, deliberately meaning-free: it records that something
  happened and what it contained, never what it implies.
- Intelligence is an **anticorruption layer** in both directions. Model outputs never enter the work
  domain except as validated tool calls; domain state enters prompts only through curated projections.

## 5. Data flow

### 5.1 Capture to structure

```
1. Source adapter / human paste
        │
        ▼
2. POST /api/v1/ingest/events      (service account or user principal)
        │  · schema validation
        │  · idempotency on (source_system, source_ref, content_hash)   [ADR-0018]
        ▼
3. Raw payload → MinIO            Event row → Postgres (immutable)
        │
        ▼
4. Outbox row: signal.event.recorded
        │
        ▼
5. Worker: normalise (participants → Person candidates, timestamps, sensitivity class)
        │
        ▼
6. Worker: enqueue extraction job (Redis stream, consumer group)
        │
        ▼
7. Agent runtime: retrieves scoped context via read tools, runs extraction
        │
        ▼
8. Tool calls → Tool Gateway → application services
        │   · authz (delegated principal ∩ agent role)
        │   · validation, blast-radius limit, idempotency
        │   · Work/Commitment/Risk/Decision created or updated OR proposal recorded
        │   · Evidence rows linking Event excerpt → entity
        │   · audit entry + AIInteraction tool-call record
        ▼
9. Outbox: work.created / commitment.recorded / proposal.raised ...
        │
        ▼
10. Worker: notifications, monitor re-evaluation, projection refresh
```

Step 8 is the only write path. Steps 7 and 8 are the only place model output exists, and it is
schema-validated before it reaches a service.

Under Decision Pack v1.0 step 8 always produces a **Proposal** for Work Core mutations, never a direct
mutation. The mutation happens later, in a separate transaction, when a human approves:

```
11. User reviews Proposal in the web UI
12. Approve / edit-and-approve / reject
        │
        ▼
13. ApprovalRecord written (approver, proposal, exact proposed action, timestamp)
        │
        ▼
14. Tool Gateway executes the approved action, actor = approving user, executed_via = ai_tool
        │
        ▼
15. Work / Commitment / Risk / Decision created; DomainEvent emitted; ApprovalRecord
    updated with the resulting mutation reference and audit entry id (immutably appended)
```

### 5.1a Capture surfaces (PQ-1)

| Surface | Principal | Path | Notes |
|---|---|---|---|
| Web manual entry and paste | the user | `POST /api/v1/events` (user-authenticated) | Mandatory MVP path. Attachments go to MinIO and are referenced by the Event |
| OpenClaw messaging (WhatsApp first) | dedicated **connector** service account | `POST /api/v1/ingest/events` | The channel adapter is an ingestion client only. It holds no tool permissions (ADR-0027) |
| Internal system activity | system actor | emitted in-process by the Work Core | Work status changes, commitments, decisions, notifications, project activity, comments (subject to N-3) |

Adding a channel means adding a `ChannelAdapter` implementation plus a connector service account. It
must require no change to the Work Core, the Tool Gateway or the domain model. That constraint is the
test of whether this layer is correctly placed.

### 5.2 Monitoring

Scheduled deterministic monitors run in `worker` against Postgres on a fixed cadence (proposed:
every 15 minutes for time-sensitive checks, hourly for graph checks). Each monitor is a named rule
with a stable identifier, a SQL/domain query, and a threshold. Monitors emit **findings**. A finding
may create or update a Risk, raise a Notification, or do nothing if already open.

The AI is invoked only after a finding exists, to draft explanation and suggest mitigation. A monitor
must never depend on a model call (P5, ADR-0012).

### 5.3 Executive read path

Rollups are computed queries over the work graph, cached in Redis with short TTL and explicit
invalidation on relevant domain events. If a narrative summary is requested, the summariser is given
the computed numbers plus cited entity references and is forbidden from introducing figures that are
not in its input.

## 6. Event and evidence architecture

Three distinct concepts share the word "event" in casual usage. They are separated here, and the
naming ambiguity is tracked as A-1 (§12).

| Concept | Meaning | Store | Mutability |
|---|---|---|---|
| **Event** (domain entity) | An observed unit of human/system activity: a meeting, a message, a document revision, a commit | `events` table + raw blob in MinIO | Immutable; corrections are new events |
| **Evidence** (domain entity) | An assertion that a specific excerpt of an Event supports a specific claim about a specific entity | `evidence` table | Immutable; superseded, not edited |
| **DomainEvent** (technical) | An internal fact emitted when state changes, e.g. `work.status_changed` | `outbox` table, then in-process/Redis dispatch | Append-only, retained for a bounded window |

The three are related but never interchangeable. The worked example from Decision Pack v1.0:

```
WhatsApp message "Anh Huy sẽ hoàn thành API IOC trước thứ Sáu."
   → Event            (immutable capture, raw payload retained)
   → AI interpretation
   → Evidence         (verbatim excerpt supporting the claim)
   → Proposal         (candidate Commitment, pending review)
   → user approval    (ApprovalRecord)
   → CommitmentCreated DomainEvent
   → Commitment state in the Work Core
```

### 6.1 Internal activity as Events, and loop prevention

Per PQ-1, the Work Core also produces Events for internal activity. A DomainEvent and an internal
Event are not the same row and not the same concept: the DomainEvent says *what changed*, the internal
Event says *that activity occurred and here is its content*, so that the timeline, evidence and AI
context are complete.

Only DomainEvents on the allow-list in `business-rules.md` §9a project into internal Events, and every
internal Event carries `origin = internal` plus `origin_domain_event_id`.

This creates a feedback path that must be cut explicitly, or the system will extract from its own
output and amplify its own errors:

```
Work created ──► DomainEvent ──► internal Event ──► extraction? ──► Proposal ──► Work created ...
                                                    └── BLOCKED by BR-E-11
```

Internal Events are never queued for AI extraction. They are available as AI *context* (read tools,
timeline) and as Evidence targets for human use, but they never trigger a capability. Extraction runs
only on Events with `origin = external`.

Properties of the design:

- **Append-only signal store.** Events are never edited or deleted except by retention policy.
  Deletion is recorded.
- **Excerpt-level provenance.** Evidence points at a locator within the Event (character range,
  transcript timestamp range, message id), so the UI can show the actual sentence behind a claim.
- **Assertion typing.** Evidence declares what it claims: `creates`, `supports`, `completes`,
  `contradicts`, `reassigns`, `reschedules`. Contradicting evidence is retained alongside supporting
  evidence rather than overwriting it.
- **Dual attribution.** Evidence records both the producing actor (an AIInteraction or a Person) and
  the confidence at production time. Human confirmation adds Evidence rather than erasing AI evidence.
- **Outbox pattern** (ADR-0005). Domain events are written in the same transaction as the state change
  and dispatched afterwards by the worker. This is what makes "every change notifies, exactly once
  logically" survivable across restarts without distributed transactions.
- **Queue.** Redis Streams with consumer groups (ADR-0006). Accepted limitation: Redis is not the
  durability boundary. The outbox is. A lost stream entry is recoverable by re-dispatch from the
  outbox; no state is lost.

## 7. API boundaries

### 7.1 Public HTTP API (`/api/v1`) — humans and first-party UI

Resource-oriented REST, OpenAPI 3.1 generated by FastAPI (ADR-0015).

```
/organizations /departments /teams /people /memberships
/projects /projects/{id}/milestones
/work            (filter: project, assignee, status, due, source, health)
/work/{id}/dependencies
/commitments
/risks  /decisions
/events  /events/{id}/evidence        (POST /events = web capture: paste, manual entry, attachments)
/evidence
/proposals       (AI-originated pending changes: approve, edit-and-approve, reject)
/proposals/{id}/approval        (immutable ApprovalRecord; Level 2 execution is triggered here)
/notifications
/insights/...    (rollups, exec views)
/ai/interactions (read-only transparency surface)
```

Conventions: cursor pagination, `problem+json` errors, `If-Match`/ETag on mutable entities to prevent
lost updates, `Idempotency-Key` on POST, explicit field selection avoided (no GraphQL in v1).

### 7.2 Ingestion API (`/api/v1/ingest`) — source systems

Separate concerns: connector service-account principals, high volume, strict idempotency,
back-pressure, payload size limits, and a `source_system` registry. Not usable to create work items
directly. Only Events.

The OpenClaw channel adapter uses this API with a **connector** identity that is distinct from the
agent identity used at the Tool Gateway (ADR-0027). Ingestion rights and tool rights are never held by
the same principal, so a compromised channel can inject content but cannot act on it.

### 7.3 Tool API (`/internal/tools`) — agent runtime only

Not routed by the public proxy. Reachable only from the agent network. Detailed in
`docs/ai/ai-architecture.md`. Tools are versioned, schema-declared, and split into `read` and
`mutate` families with different controls.

### 7.4 Internal boundaries

- Routers never import repositories.
- Application services never import FastAPI.
- Contexts never import another context's internals. CI enforces with an import-linter contract.
- The `tools` package may import application services only, never contexts directly.

## 8. Frontend architecture

- React + TypeScript + Vite, TanStack Query for all server state, no global client store for
  server-owned data.
- OIDC authorization-code + PKCE against Keycloak; access token in memory, refresh via the IdP.
- API client generated from the OpenAPI schema, regenerated in CI. Drift fails the build.
- Three surface groups:
  1. **Work surfaces** — full manual CRUD. These must work with AI off (P1).
  2. **Review surfaces** — proposal inbox, evidence viewer, confirmation flows. This is where
     "minimise typing" is actually delivered.
  3. **Intelligence surfaces** — dashboards, risk views, rollups, interaction transparency.
- Every AI-originated value in the UI is visually attributed and one click from its evidence.

## 9. Deployment architecture

Docker Compose for development and pilot (ADR-0020). Networks are a security control, not just
plumbing.

```
networks:
  edge      : proxy, web, keycloak
  app       : proxy, api, worker, keycloak
  data      : api, worker, postgres, redis, minio        ← agent-runtime NOT present
  agent     : agent-runtime, api (internal tool port only)
  egress    : agent-runtime → model provider (allow-list)
```

Consequences:

- The agent runtime cannot resolve or reach `postgres`, `redis` or `minio`. Rule 1 in `CLAUDE.md` is
  enforced by topology, not by discipline.
- The agent reads raw payloads only through short-lived presigned URLs issued by the API after an
  authorization check.
- Only `proxy` publishes ports to the host.

Services, resource intent and state:

| Service | Stateful | Notes |
|---|---|---|
| proxy (Caddy or Traefik) | no | TLS termination, routes `/api`, `/realms`, `/` |
| web | no | static build served by proxy or nginx |
| api | no | uvicorn workers; horizontal scaling is trivial |
| worker | no | queue consumers; concurrency configurable per queue |
| scheduler | no | single instance, leader-locked via Redis |
| agent-runtime | no | OpenClaw; isolated network; model API key lives only here |
| postgres | yes | primary store; pgvector extension; PITR via WAL archiving |
| redis | yes (ephemeral) | cache, streams, locks; may be lost without data loss |
| minio | yes | raw payload store; versioning on; lifecycle rules for retention |
| keycloak | yes | own database schema or instance |

Backups: nightly logical dump plus continuous WAL archive for Postgres; MinIO bucket replication or
scheduled sync; Keycloak realm export in version control (`ops/keycloak/realm.json`).

Environments: `dev` (compose, seeded, fake model), `pilot` (compose on one host, real model),
`prod` (out of scope for v1; migration path to Kubernetes documented but not built — ADR-0020).

## 10. Observability

- Structured JSON logs with `trace_id`, `org_id`, `actor`, and where relevant `ai_interaction_id`.
- OpenTelemetry traces across HTTP → service → DB, and across ingestion → worker → agent → tool call,
  so a single captured meeting can be traced end-to-end to the work items it produced.
- Metrics: ingestion lag, extraction latency and cost, tool call volume by tool and outcome, proposal
  acceptance/rejection/reversal, monitor run duration, notification delivery, queue depth.
- The AI transparency surface in the product is built from the same data as the operational metrics.
  There is no hidden AI telemetry.

## 11. Scalability and failure behaviour

| Failure | Behaviour |
|---|---|
| Agent runtime down | Ingestion continues; extraction jobs queue; Work Core fully usable. No data loss. |
| Model provider down or rate-limited | Jobs retry with backoff and a dead-letter after N attempts; visible as ingestion backlog, not as errors in the work UI |
| Redis lost | Cache cold, streams lost; outbox re-dispatch restores pending work; no state loss |
| Postgres down | System is down. This is the accepted single point of failure at pilot scale |
| MinIO down | Evidence excerpts still readable (stored inline in `evidence`); raw payload viewing degraded |
| Tool call authorization failure | Rejected, audited, surfaced in the AIInteraction record as a failed step |

Deliberate non-goals at this scale: multi-region, read replicas, sharding, a separate search cluster,
per-context databases.

## 12. Architectural risks and ambiguities

### 12.1 Resolved by Decision Pack v1.0

| # | Item | Resolution |
|---|---|---|
| A-1 | "Event" meant three things | `Event`, `Evidence`, `DomainEvent` kept distinct; internal activity projects into Events under §6.1 (ADR-0004, ADR-0028) |
| A-7 | Work without a Project | Project is optional on Work and fully functional without one (ADR-0029) |
| A-9 | Tenancy undecided | Multi-organization capable schema, authorization and API; single-org deployment for the MVP (ADR-0008, ADR-0031) |

### 12.2 Open

| # | Item | Risk | Options / mitigation |
|---|---|---|---|
| A-2 | Commitment may be a separate aggregate or a facet of Work | Either duplication or loss of the social promise semantics | (a) separate aggregate linked to optional Work (proposed); (b) Work with `is_commitment`; revisit after first extraction evals |
| A-3 | OpenClaw now has two roles: capability runtime and WhatsApp channel adapter, and its contract is still unspecified | Integration surprises, and a blurred boundary between "the thing that reasons" and "the thing that receives messages" | Spike before Phase 3. Model the two roles as two ports with two principals (ADR-0027). If OpenClaw cannot separate them, report before building |
| A-4 | Extraction quality unknown until real data | Core value may not materialise | Phase 2 collects a labelled corpus before Phase 3 builds on it; eval gates in `docs/ai/ai-architecture.md` |
| A-5 | Identity resolution across sources, now MVP-critical because WhatsApp gives phone numbers rather than names (PQ-7) | Wrong attribution damages trust worse than missing data | Confidence-gated matching with human confirmation; never auto-attribute a commitment below threshold (BR-I-06) |
| A-6 | Duplicate entity creation | Work graph degrades into noise | Mandatory link-or-create tool semantics with candidate search (ADR-0017, BR-AI-05) |
| A-8 | Redis Streams durability | Perceived data loss | Outbox is the durability boundary; document and test re-dispatch (ADR-0006) |
| A-10 | Notification volume, now larger because notifications themselves generate Events | Users disable the product; event table inflates | Deterministic dedup keys, digesting, per-monitor rate limits; notification-derived Events are internal-origin and never extracted (BR-E-11) |
| A-11 | Prompt injection via ingested content, sharper on an open channel like WhatsApp where anyone with a number can send text into the pipeline | AI induced to propose malicious work, or to leak context | Level 1-2 autonomy means injected content can at most produce a Proposal a human must approve. Plus content fencing, allow-listed tools, blast radius, adversarial evals. **Channel senders unknown to the organization are a new threat surface (T-11).** |
| A-12 | Legal posture on workforce observation (PQ-4), now involving a personal messaging channel | Deployment blocker; capture of non-employees' messages | Per-channel consent and scope controls designed now; decide before the WhatsApp adapter ships |
| A-13 | Compose-only deployment | Single host, no rolling deploys | Accepted for pilot; documented migration path |
| A-14 | Internal-Event volume and loop risk | Event table dominated by internal noise; self-amplification | Allow-list of projecting DomainEvents (§9a of business rules), `origin` partitioning, extraction restricted to external origin |
| A-15 | Vietnamese-language extraction and verbatim excerpt verification (N-2) | Evidence verification and date normalisation behave differently across languages and scripts | Decide target languages before prompt work; eval corpus must match the pilot's actual language mix |

## 13. What we are explicitly not building in v1

Microservices, event sourcing of the work domain (outbox ≠ event sourcing), CQRS with separate read
stores, GraphQL, a plugin system, an in-house model, multi-model routing, autonomous action in
external systems, mobile apps.
