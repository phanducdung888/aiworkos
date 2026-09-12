# Progress — AI WorkOS

Last updated: 2026-09-11 · Current phase: **Phase 0 — Architecture**

### `PHASE 1 READY — DOCUMENTATION BASELINE COMPLETE`

Decision Pack v1.0 and Resolution Pack v1.1 are applied. No material blockers remain for Phase 1.
Implementation has not started and must not start until this baseline is signed off.

This file is the single source of truth for what is built, what is next, and what is blocked. Update
it in the same change as the work it describes.

---

## Status at a glance

| Phase | Name | Status | Exit gate |
|---|---|---|---|
| 0 | Architecture & product definition | ✅ complete; packs v1.0 and v1.1 applied | **met** |
| 1 | Work Core (no AI) | 🟡 in progress — checkpoints 1 and 2 complete | A team can manage real work in it with AI off |
| 2 | Signal capture & evidence | ⬜ not started | Events ingested, evidence attachable by hand, corpus collecting |
| 3 | Tool Gateway & extraction | ⬜ not started | Proposals from real activity, accepted by real users |
| 4 | Monitoring, risks, commitments | ⬜ not started | System raises a risk a human had not noticed |
| 5 | Executive intelligence | ⬜ not started | A lead stops writing status reports by hand |
| 6 | Connectors & autonomy tuning | ⬜ not started | Capture is passive; autonomy promoted on evidence |

## Phase 0 — Architecture & product definition ✅

Delivered:

- [x] `CLAUDE.md` — working agreement and non-negotiables
- [x] `docs/product/product-constitution.md`
- [x] `docs/architecture/system-architecture.md`
- [x] `docs/domain/domain-model.md`
- [x] `docs/domain/business-rules.md`
- [x] `docs/ai/ai-architecture.md`
- [x] `docs/security/security-model.md`
- [x] `docs/testing/testing-strategy.md`
- [x] `docs/development/definition-of-done.md`
- [x] `docs/development/progress.md`
- [x] `docs/decisions/ADR-INDEX.md`

Not delivered, by design: no code, no schema, no compose file.

Also delivered: `docs/decisions/decision-pack-v1.0.md`.

**Resolved by Decision Pack v1.0:** PQ-1, PQ-2, PQ-3, A-1, A-7.

**Exit criteria before Phase 1 starts**

- [x] PQ-1 capture sources · web paste and manual entry, OpenClaw/WhatsApp, internal Events
- [x] PQ-2 tenancy · multi-org capable, single-org deployment, shared realm
- [x] PQ-3 autonomy · Levels 1–2
- [x] A-1 Event / Evidence / DomainEvent kept distinct
- [x] A-7 Project optional on Work
- [x] **C-1** answered · Evidence attachment and Risk narrative are Level 1; non-persisted summaries
      need no Proposal (ADR-0034)
- [x] **M-1 / ADR-0032** confirmed · WorkAssignment canonical, many per Work, one active OWNER
- [x] **N-3** answered · comments are Events, no Comment entity (ADR-0033)
- [x] Optional Project semantics extended · no synthetic containers, partitioned reporting
- [ ] **N-2** target languages · blocks Phase 3 prompt and eval work, **not Phase 1**
- [ ] Architecture reviewed and signed off by the product owner and engineering

## Phase 1 — Work Core, no AI

Goal: a usable work management system that proves principle P1. If this phase is not independently
valuable, the architecture is wrong.

Scope:
- Repository skeleton, compose stack (postgres, redis, keycloak, minio, api, web, proxy), CI.
- Identity & Organization context: Organization, Department, Team, Person, memberships, roles.
- Keycloak realm, OIDC login, Person provisioning.
- `platform/`: db session, authz policy, audit, outbox, telemetry, config.
- Work Core: Project, Milestone, Work (Project optional), WorkAssignment (canonical, OWNER /
  CONTRIBUTOR / REVIEWER), Dependency, with all rules in `business-rules.md` §3–§5.
- `work_current_owner` read model, and the partitioned reporting queries in §11a (BR-RPT-01 to -05).
- Organization membership resolution and the tenant isolation suite, with two seeded organizations from
  the first migration onward.
- Public API `/api/v1` for the above, OpenAPI, generated client.
- Web UI: projects, milestones, work board and list, dependency view, manual CRUD throughout.
- Tests: L1, L2, L3, L5, L7 including the authorization matrix.

Acceptance: a real team runs a real project in it for a week without the words "AI" or "ingest"
appearing anywhere in the system, including work that belongs to no Project and has no assignee. The
tenant isolation suite passes with application scoping disabled, and the reporting partition is visible
in the UI.

## Phase 2 — Signal capture & evidence

Scope:
- Signal context: Event, EventParticipant, Evidence; MinIO raw payload storage.
- Ingestion API with idempotency, sensitivity classification, retention fields.
- Web capture surface (the mandatory MVP path, PQ-1): manual message entry, pasted conversation, pasted
  meeting notes, pasted email, file attachments.
- Internal Event projection from the allow-listed DomainEvents, with the loop-prevention rule and its
  tests in place before any AI exists to be trapped by it.
- Manual evidence attachment: a human links an excerpt to a work item. This validates the evidence
  model before AI depends on it.
- Outbox dispatch and worker process in production use.
- Begin collecting the labelling corpus for evals (AI-4).

Acceptance: an Event ingested twice produces one Event; a human can trace a work item to the sentence
that justified it.

## Phase 3 — Tool Gateway & AI extraction

Depends on: PQ-1 answered, OpenClaw spike done (A-3), corpus from Phase 2.

Scope:
- Tool Gateway with authentication, delegation, validation, blast radius, idempotency, audit.
- Read and mutation tools per `docs/ai/ai-architecture.md` §4, plus the fake runtime.
- Intelligence context: AIInteraction, ToolCall, Proposal, ProposedChange.
- OpenClaw integration behind the `AgentRuntime` port; extraction (C1) and linking (C2), at Level 1.
- ApprovalRecord and the Level 2 execution path with hash verification, single use and expiry.
- Review UI: proposal inbox, evidence viewer, approve / edit-and-approve / reject.
- Eval harness in CI with golden, negative, ambiguity, linking and adversarial sets.
- Network segmentation and the L7 "agent has no DB access" tests, enforced from day one of this phase.

Acceptance: the PQ-1 acceptance criterion passes end to end, and proposals generated from real activity
are approved at a rate that beats typing, with invention rate under the gate.

### Phase 3b — OpenClaw WhatsApp channel

Split out because it carries different risk from extraction itself and is blocked by different
questions.

Scope: `ChannelAdapter` port, the WhatsApp adapter, the connector service account, sender policy,
phone-number identity resolution (PQ-7), and the channel eval set.

Blocked by: S-7 (channel sender policy), PQ-4 and T-12 (third-party capture), AI-1 (whether OpenClaw
can hold adapter and runtime as separate principals).

Acceptance: a WhatsApp message produces a Proposal a human approves, with no change required to the
Work Core, the Tool Gateway or the domain model.

## Phase 4 — Monitoring, risks, commitments

Scope:
- Commitment context and lifecycle, including dispute handling.
- Governance context: Risk and Decision.
- Deterministic monitors MON-001…MON-012 with the scheduler.
- Notification context, dedup, digesting, delivery-time authorization.
- AI capability C4 (risk explanation) and C3 (progress maintenance).

Acceptance: the system surfaces at least one real risk before a human raised it, with a traceable
rule and evidence.

## Phase 5 — Executive intelligence

Scope: rollups and caching, project and portfolio health derivation, executive dashboards, C5
summarisation over computed numbers, AI transparency surface, and C6 question answering if PQ-6 says
so.

Acceptance: a department lead produces a weekly status in under five minutes, with drill-down to
evidence.

## Phase 6 — Connectors & autonomy tuning

Scope: the first passive source connectors per PQ-1, identity resolution at scale, autonomy promotion
based on measured online metrics, cost tuning, retention and DSR procedures, performance work.

Acceptance: manual entry ratio below 30%, reversal rate below 5%.

---

## Open questions register

### Open

**An archived Team or Department still accepts new work.** `assert_team_exists` checks tenancy, not
status, so a team archived this morning can own a Project created this afternoon. BR-I-05 settles
the analogous question for people — a departed Person takes no new work — and no rule states the
unit equivalent, so this is undecided rather than decided. Pinned by
`test_an_archived_team_can_still_be_given_new_work_today` so that changing it is visible.

### Resolved

**Assigning an unknown person returned 404 (2026-09-12).** `AssignmentService._create` raised
`EntityNotFound` for a `person_id` that did not resolve, so a client was told the Work item it had
just addressed did not exist. The addressed resource was present; the unresolvable thing was a body
field. Now `BR-G-01`, matching every other reference check under ADR-0035. No test pinned the old
status, which is why it survived Checkpoint 4a.

**Checkpoint 5.1 (2026-09-12).** W-11 resolved by ADR-0035: Work Core validates every Identity reference through `identity.public` before it writes, so a bad `owning_team_id` is a `BR-G-01` rule violation naming the field rather than a composite foreign key violation from psycopg. The dependency runs one way — Work Core depends on Identity, Identity depends on nothing — enforced by the import-linter contract and two architecture tests. W-12 resolved: Identity has a domain layer (BR-I-01 to BR-I-07), repositories, seven application services and a write API. W-13 resolved by ADR-0036: people are provisioned administratively and there is no just-in-time creation, because a shared realm means JIT would let any authenticated subject conjure themselves a Person in any organization they name in a header. ADR-0037 gives `ExternalIdentity` its own authorization resource with no SELF grant anywhere.

**Checkpoint 5 (2026-09-12).** Frontend: React + TypeScript + Vite, TanStack Query for all server state, OIDC authorization-code with PKCE, a client generated from `backend/openapi.json` with a CI drift check. Work surfaces cover capture, the partitioned list, work detail with status, assignment and dependencies, and projects with milestones. L5 component tests include axe checks; L6 covers journeys 1, 1a, 1b and 4 — the four the Phase 1 domain can complete. T-3's resolution is reused for the browser: the journeys mint real RS256 tokens against a JWKS the dev server publishes, so the API runs its production verification with no Keycloak in the loop.

A read-only Identity API (`/me`, `/people`, `/teams`, `/departments`) was built as part of this checkpoint rather than as new scope: Phase 1 already lists the Identity context and its public API, and journeys 1 and 1a cannot be completed without it — `POST /projects` requires an owning Team (BR-P-01) and an assignment requires a Person, and nothing listed either. It uses only existing matrix cells and needed no migration.

**Checkpoint 4b (2026-09-12).** W-6: `Idempotency-Key` implemented, optional on every POST, recorded in the mutation's own transaction. W-7: BR-W-19 written and enforced at creation, at a later visibility change, and as a cascade when a Project narrows; migration 0007 backfills rows that predate it. W-8: confirmed — authorship reaches READ and LIST. Project visibility, previously undefined, is now BR-P-09.

**Checkpoint 4a (2026-09-12).** T-3 resolved as option (a): tests sign their own tokens, the JWKS is stubbed, verification is real. The missing implementation contract is now `docs/development/phase-1-implementation-contract.md` and every `contract §n` citation in the codebase was checked against it — one was wrong (`work/public.py` cited §12 for a data-access boundary rule, which is §13) and is corrected. Work visibility, previously undefined anywhere, is now BR-W-18.

**Checkpoint 3.5 (2026-09-12).** W-1: `created_by_person_id` added to all five Work Core tables by migration 0006; the `PERSONAL` relation on Work is now assignment **or** authorship. PO decision, as amended by W-8 at Checkpoint 4a: authorship is permanent and reaches `READ`, `LIST`, `UPDATE` and `CHANGE_STATE`, plus `ASSIGN` under the paired condition in D1 (Checkpoint 4b) — the creator may claim the Work for themselves and may not staff it to anybody else. It does not reach `CHANGE_VISIBILITY`, `REASSIGN` or `END_ASSIGNMENT`. W-2: BR-D-03 reworded and MON-007a added; the narrow reading stands and the inconsistent state it leaves behind is now detected rather than ignored. W-3: BR-W-17 written. W-4: `outbox.seq` added and `relay_once` orders by it.

| id | Decision | Source |
|---|---|---|
| PQ-1 | Web capture (mandatory) + OpenClaw/WhatsApp + internal Events; no other connectors | Decision Pack v1.0 |
| PQ-2 | Multi-org capable; single-org deployment; shared Keycloak realm | Decision Pack v1.0 |
| PQ-3 | Autonomy Levels 1–2 with recorded approvals | Decision Pack v1.0 |
| A-1 | Event / Evidence / DomainEvent kept distinct; internal activity projects into Events | Decision Pack v1.0 |
| A-7 | Project optional on Work | Decision Pack v1.0 |
| A-9 | Folded into PQ-2 | Decision Pack v1.0 |
| C-1 | Evidence attachment Level 1; Risk narrative Level 1; non-persisted summaries need no Proposal | Resolution Pack v1.1 → ADR-0034 |
| M-1 | WorkAssignment canonical; many assignments; one active OWNER; no mirror column | Resolution Pack v1.1 → ADR-0032 accepted |
| N-3 | Comments are Events; no Comment entity | Resolution Pack v1.1 → ADR-0033 |
| M-7 | Folded into N-3 | Resolution Pack v1.1 |
| Optional Project semantics | No synthetic Projects; reporting partitioned; rollups exclude non-project Work | Resolution Pack v1.1 → ADR-0029 extended |
| S-1 | Shared product realm | Decision Pack v1.0 → ADR-0031 |
| AI-2, AI-7 | Superseded by PQ-3 | Decision Pack v1.0 |

### Open

Owner and due date to be filled at Phase 0 sign-off.

| id | Question | Source | Blocks | Priority |
|---|---|---|---|---|
| N-2 | Target languages for MVP-quality extraction | product-constitution §10 | prompts, eval corpus, Phase 3 | **high** |
| W-14 | **No `web` or `proxy` service in Compose.** Phase 1 scope lists both. The frontend runs from `npm run dev` and the E2E harness starts it, so nothing is blocked today, but there is no container image for it and no reverse proxy terminating one origin in front of both. | progress.md Phase 1 scope, docker-compose.yml | before the pilot | medium, new |
| W-15 | **Five of the nine curated journeys are unwritten.** Journeys 2, 3, 5, 6 and 7 need Events, Proposals, Commitments or Risks, which is Phase 2 and later. Journey 8 (AI disabled) is satisfied by construction — there is no AI to switch off — and is recorded rather than coded. | testing-strategy §L6 | Phase 2+ | low, new |
| W-9 | **`idempotency_key` has no retention job.** Records are kept for the 24 hours a client retry needs and nothing deletes them afterwards, so the table grows with every keyed POST. The application role is granted SELECT and INSERT only, deliberately — a stored response is the answer that was given — so the sweeper runs as a different role. | migration 0007 | Phase 2 | medium, new |
| W-10 | **Two concurrent requests sharing one Idempotency-Key.** The unique constraint catches the second at insert, after its mutation has run in the same transaction, so that transaction rolls back and the caller gets 409 with `Retry-After: 1`; the retry then replays the first response. Correct but pessimistic — it wastes the work already done. A reservation row inserted before the handler would serialise them properly, at the cost of a second round trip on the common path. | `platform/http/idempotency.py` | Phase 2 | low, new |
| W-5 | **`outbox.seq` orders within a transaction, not globally.** A bigserial is assigned at insert, but transactions commit in a different order than they start, so a relay reading between two commits can pass a `seq` that is still uncommitted and see it appear behind it afterwards. Causal ordering is safe — events from one transaction always arrive in append order — and a single relay at pilot volume is fine. Multiple concurrent consumers, or any guarantee of a strict global order, needs `pg_current_snapshot()` watermarking or an advisory lock around the claim. A recorded trade-off, not an oversight. | `platform/outbox.py`, migration 0006 | Phase 2 relay with a real transport | medium, new |
| N-4 | Should `COMMENT` Events be extraction-eligible despite internal origin? | domain-model §13 | Phase 2 comment UI, Phase 3 extraction scope | **high, new** |
| M-10 | Comments are immutable Events (BR-E-01), so editing or deleting a comment means a new Event. Is that acceptable product behaviour? | ADR-0033 consequences | Phase 2 UI | medium, new |
| S-7 | Channel sender policy: who may send into a connected channel | security-model §9 | Phase 3b | high, new |
| N-1 | Outbound messaging in the MVP | product-constitution §10 | Phase 3b, tool catalogue | medium, new |
| PQ-4 | Workforce observation legal posture, now including personal-device messaging | product-constitution §10 | Phase 3b, pilot | **high, raised by PQ-1** |
| PQ-7 | External identity resolution, now phone numbers | product-constitution §10 | Phase 3, Phase 3b | **high, raised by PQ-1** |
| A-3 / AI-1 | OpenClaw contract, now with two roles | architecture §12.2 | Phase 3, Phase 3b | high, spike required |
| AI-3 | pgvector in v1 | ai-architecture §11 | Phase 3 | medium |
| AI-4 | Golden set origin before real data | ai-architecture §11 | Phase 2 | medium |
| AI-6 | Model hosting, entangled with PQ-4 | ai-architecture §11 | Phase 3 | medium |
| PQ-5 | Effort/capacity modelling | product-constitution §10 | MON-008, Phase 4 | medium |
| PQ-6 | Q&A in v1 | product-constitution §10 | Phase 5 | low |
| A-2 | Commitment as a separate aggregate | architecture §12.2 | Phase 4 | low, revisit after Phase 3 evals |
| A-14 | Internal-Event volume and loop risk | architecture §12.2 | Phase 2 | medium, mitigated by BR-E-11 |
| M-8, M-9 | Channel thread id, phone identity typing | domain-model §13 | Phase 3–3b | low |
| S-5 | DSR procedure | security-model §9 | pilot | medium |
| S-8 | Notifying non-user participants of a captured group | security-model §9 | Phase 3b | tied to PQ-4 |
| T-1, T-5, T-6 | CI topology, channel adapter testing, native-speaker labelling | testing-strategy §9 | Phase 1–3 | low–medium |

## Phase 1 checkpoint log

| Checkpoint | Scope | Status |
|---|---|---|
| 1 | Migrations 0001–0002, authorization model and matrix, RLS, audit, outbox, architecture tests | ✅ complete · 76 tests |
| 2 | Migrations 0003–0005, Work Core schema and pure domain layer, read models | ✅ complete · 151 tests total |
| 3 | Application services for Project, Milestone, Work, WorkAssignment, Dependency | ✅ complete · 185 tests total |
| 3.5 | Defect and debt: W-1 creator provenance, W-4 outbox ordering, W-3 and W-2 rule text, the ASSIGN/REASSIGN bypass | ✅ complete · 195 tests total |
| 4a | HTTP spine and the Work vertical slice: auth, principal, problem+json, ETag, cursor pagination, read side | ✅ complete · 238 tests total |
| 4b | Project, Milestone, Dependency and ownership routers, visibility inheritance, idempotency, OpenAPI snapshot, schemathesis | ✅ complete · 299 tests total |
| 5 | Frontend (Work surfaces), generated client, L5 component tests, L6 journeys 1/1a/1b/4, Identity read API | ✅ complete · 334 backend + 26 component + 5 journeys |
| 5.1 | Identity & Organization write side: domain, repositories, services, REST API; W-11 reference validation; ADR-0035/0036/0037; AuthProvider tests | ✅ complete · 429 backend + 38 frontend |

Checkpoint 2 delivered: `project`, `milestone`, `work`, `dependency`, `work_assignment`, the
`work_current_owner` and `work_partitioned` views, and `app/contexts/work/domain.py`. No application
services, no API, no UI.

Database-level invariants now enforced independently of application code: work hierarchy depth and
acyclicity, dependency acyclicity over active `blocks` edges, blocked-requires-a-cause, single active
OWNER, single active primary assignment, milestone-belongs-to-the-same-project, and no assignment to
a departed person. Each was verified by disabling the control and confirming the violation becomes
possible.

Checkpoint 3 delivered the write path: `app/contexts/work/{commands,repository,authorization,
services}.py`, `app/contexts/identity/queries.py` and `app/platform/concurrency.py`. Every use case
runs authorize → domain validation → repository → audit → outbox, in that order, inside a
transaction the caller owns. No API layer and no read/list services; both are Checkpoint 4.

Relationship facts for the matrix (`IN_TEAM`, `IN_DEPARTMENT`, `PERSONAL`) are resolved once per
request by `authorization.reach_of` and supplied to the policy engine, which still never queries the
database. Cross-context access to Identity goes through `identity.public` and is the first such edge
in the codebase; the import-linter `independence` contract was stricter than ADR-0001 and now names
that one edge as its only exception.

Cascades implemented: BR-P-04 (a cancelled project cancels its open milestones and work, with the
originating action recorded on the cascaded audit entries), BR-D-03 and BR-D-05 (a blocker reaching
`done`/`achieved` resolves its dependencies, reaching `cancelled`/`rejected` withdraws them).

## Phase 1 readiness review

Status: **PHASE 1 READY — DOCUMENTATION BASELINE COMPLETE**. Coding still awaits sign-off.

### Tables required for Phase 1

| Group | Tables |
|---|---|
| Platform | `organization`, `audit_entry`, `outbox` |
| Identity | `person`, `organization_membership`, `department`, `team`, `team_membership`, `role_assignment`, `external_identity` |
| Work Core | `project`, `milestone`, `work`, `work_assignment`, `dependency` |
| Read models | `work_current_owner` (view over `work_assignment`) |

Not in Phase 1: `event`, `event_participant`, `event_attachment`, `evidence`, `commitment`, `risk`,
`decision`, `proposal`, `proposed_change`, `approval_record`, `ai_interaction`, `tool_call`,
`notification`. Their columns are specified so the Phase 1 schema does not paint them into a corner.

### Migration order

1. `0001_extensions_and_platform` — `pgcrypto`/uuid, `organization`, `audit_entry`, `outbox`, the
   `org_id` session variable helper and the RLS policy template.
2. `0002_identity` — `person`, `organization_membership`, `department`, `team`, `team_membership`,
   `role_assignment`, `external_identity`, with RLS enabled on each.
3. `0003_work_core` — `project`, `milestone`, `work` (nullable `project_id`), `dependency`.
4. `0004_work_assignment` — `work_assignment` plus the partial unique index enforcing one active
   `OWNER` per Work and one active `is_primary`.
5. `0005_read_models` — `work_current_owner` view and the supporting indexes.
6. `0006_seed_reference` — enum reference data only. **No synthetic Project, ever** (BR-P-07a).

Constraints that must land in the same migration as their table, never "later": `org_id` NOT NULL, RLS
policy, the single-active-OWNER index, and the dependency acyclicity trigger or check.

### Authorization matrix required for Phase 1

Dimensions: `role` × `scope relationship` × `resource` × `action`, with `organization` as a term in
every cell.

- Roles: `org_admin`, `department_lead`, `team_lead`, `member`, `viewer`, `auditor`, `executive`.
- Scope relationships: same org / different org; in department subtree; in owning team; is OWNER of the
  Work; is CONTRIBUTOR; is REVIEWER; unrelated.
- Resources: Organization, Department, Team, Person, OrganizationMembership, RoleAssignment, Project,
  Milestone, Work (project and non-project variants), WorkAssignment, Dependency, AuditEntry.
- Actions: create, read, list, update, delete-equivalent (state change), assign, reassign, end
  assignment, change visibility.

Two rows deserve explicit attention because they are new: **non-project Work** has no project lead to
inherit authority from, so its authority derives from the OWNER, the capturing user and their team
lead; and **WorkAssignment** is a separate resource, so "can edit Work" and "can change who owns Work"
are different permissions.

### Architecture tests required before the first business migration

These must exist and pass before `0003_work_core` lands, because each one is cheap now and expensive to
retrofit:

| Test | Guards |
|---|---|
| `test_every_table_has_org_id` | BR-G-01 |
| `test_rls_policies_present` | BR-G-01a |
| `test_tenant_isolation_without_application_scoping` | PQ-2 acceptance criteria |
| `test_neither_role_can_bypass_row_level_security` | the precondition for the row above: FORCE RLS is inert for a superuser or BYPASSRLS role |
| `test_no_assignee_column_on_work` | BR-W-12, ADR-0032 |
| `test_single_active_owner_constraint_exists` | BR-W-13 |
| `test_project_is_optional_on_work` | ADR-0029 |
| `test_no_synthetic_projects_in_seed_or_migration` | BR-P-07a |
| `test_no_comment_table` | ADR-0033 |
| `test_contexts_do_not_cross_import` | ADR-0001 |
| `test_routers_have_no_business_logic` | layering |
| `test_every_mutating_service_call_writes_exactly_one_audit_entry_for_its_own_resource` | BR-G-02 |
| `test_migrations_apply_and_rollback` | migration hygiene |

The AI-boundary tests (`test_agent_has_no_db_access`, `test_tools_do_not_import_repositories`,
`test_no_autonomy_above_level_2`, `test_internal_events_never_enqueued`) are Phase 3 gates, but the
import-linter contract file should be created in Phase 1 so the boundaries exist before there is
anything to violate them.

## Decision log

| Date | Decision | Where |
|---|---|---|
| 2026-09-11 | Migrations moved off the cluster superuser onto a dedicated `workos_owner` (`NOSUPERUSER NOBYPASSRLS`). The superuser owned the schema, so `FORCE ROW LEVEL SECURITY` was not enforced on the owner path and the five `[owner_role]` isolation tests failed; they were proving a control the environment did not have. Implements ADR-0008 layer 2, no new decision. Guard test added; 153 green | `ops/db/dev-roles.sql`, `Makefile`, `docker-compose.yml`, `backend/` |
| 2026-09-11 | PostgreSQL published on `127.0.0.1:5432`. The host-side workflow the Makefile and README already assumed had no route to the container | `docker-compose.yml` |
| 2026-09-12 | Checkpoint 5: frontend, generated client, L5 and L6 tests, read-only Identity API. `make check` now runs the frontend gate as well as the backend one | `frontend/`, `backend/app/api/v1/identity.py`, `ops/dev/seed.py` |
| 2026-09-12 | `ops/dev/seed.py` runs as the cluster superuser, and the docstring says why: resolving a slug to an organization is a question from outside every tenant, and RLS means no tenant-scoped role can answer it. `workos_owner` is NOSUPERUSER and NOBYPASSRLS by design (Checkpoint 3.5), which is exactly what makes it unsuitable here | `ops/dev/seed.py` |
| 2026-09-12 | Checkpoint 4b: migration 0007 (BR-W-19 backfill, `idempotency_key`, dropped the dead `ix_outbox_unpublished`), routers for Project, Milestone, Dependency and ownership, committed OpenAPI snapshot, schemathesis. BR-W-19 and BR-P-09 written. 299 tests green | `backend/app/api/v1/`, `backend/openapi.json`, `docs/domain/business-rules.md` |
| 2026-09-12 | Schemathesis found three shape defects on its first run, all fixed at source: `type`, `priority` and `visibility` were free strings on the wire with CHECK constraints underneath, so a bad value reached PostgreSQL and returned 500; Starlette's own 404 and 405 bypassed the problem+json handler and answered in a second error shape; and the OpenAPI document published FastAPI's default validation schema while the application returns problem+json | `app/api/v1/schemas.py`, `app/platform/http/errors.py` |
| 2026-09-12 | Checkpoint 4a: HTTP spine (`platform/auth`, `platform/principal`, `platform/http/*`) and the Work vertical slice (`api/v1/work`, `contexts/work/queries`). BR-W-18 written to define Work visibility, which the document set specified nowhere. 238 tests green | `backend/app/api/`, `backend/app/platform/http/`, `docs/domain/business-rules.md` |
| 2026-09-12 | `DomainRuleViolation` and `EntityNotFound` moved from the Work Core to `platform/errors`. The HTTP error handler has to translate them and platform sits below every context, so leaving them in a context made the layering contract fail for a real reason rather than a technicality. `StaleVersionError` was already there; this makes the three consistent | `app/platform/errors.py` |
| 2026-09-12 | The `api-purity` import contract now runs with `allow_indirect_imports`. It forbade any chain reaching `sqlalchemy`, which is every service and every query there will ever be; contract §12 forbids a router from executing SQL, not from calling something that eventually does. The direct-import rule is enforced by `test_routers_are_thin`, which was tightened to also forbid the policy engine and any context internal | `backend/.importlinter` |
| 2026-09-12 | Checkpoint 3.5: migration 0006 (`created_by_person_id` on the five Work Core tables, `outbox.seq`), authorship relation, ASSIGN/REASSIGN bypass closed, BR-W-17 added, BR-D-03 reworded, MON-007a specified. 195 tests green | `backend/`, `docs/domain/business-rules.md` |
| 2026-09-12 | The ASSIGN/REASSIGN bypass was fixed in the relation computation rather than by denying `member` END_ASSIGNMENT in the matrix. PERSONAL on a WorkAssignment now means *this assignment is mine*; it previously inherited the work's relation, which let any member on a work item end any other member's assignment and then assign themselves. The matrix cell was never the enabler, and denying it would have removed a real capability — stepping off work you hold — without closing the hole's cause | `app/contexts/work/authorization.py` |
| 2026-09-12 | Checkpoint 3 implemented: Work Core application services, Identity read-side queries, optimistic concurrency. 185 tests green. Four readings taken where the rules were silent or the schema disagreed — all four recorded as open questions below rather than settled in code | `backend/app/contexts/`, `backend/app/platform/concurrency.py` |
| 2026-09-12 | Import-linter `context-isolation` contract now permits `app.contexts.work.* -> app.contexts.identity.public`. The contract forbade any cross-context import; ADR-0001 and `test_contexts_do_not_import_each_other_directly` forbid reaching past a context's published interface. The contract was wrong, not the rule | `backend/.importlinter` |
| 2026-09-11 | Phase 0 architecture set drafted; 25 ADRs proposed | `docs/decisions/ADR-INDEX.md` |
| 2026-09-11 | Decision Pack v1.0 applied: PQ-1, PQ-2, PQ-3, A-1, A-7 resolved; ADRs 0026–0032 added; ADR-0011 amended by ADR-0030, ADR-0007 amended by ADR-0031 | `docs/decisions/decision-pack-v1.0.md` |
| 2026-09-11 | Checkpoint 2 implemented: migrations 0003–0005, Work Core schema, pure domain layer, read models. 151 tests green. Person-tenancy contradiction resolved as a documentation correction in `security-model.md` §2 | `backend/` |
| 2026-09-11 | Checkpoint 1 implemented: migrations 0001–0002, authorization matrix, RLS, audit, outbox | `backend/` |
| 2026-09-11 | Resolution Pack v1.1 applied: C-1, M-1 and N-3 resolved; ADR-0032 accepted; ADR-0033 and ADR-0034 added; ADR-0029 extended with no-synthetic-Project and partitioned reporting. **Phase 1 declared ready.** New questions raised: N-4, M-10 | `docs/decisions/resolution-pack-v1.1.md` |
