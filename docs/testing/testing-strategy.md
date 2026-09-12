# Testing Strategy — AI WorkOS

Version 0.1 · Status: proposed

Testing here has an unusual job: as well as proving the code works, it must prove that the
architectural invariants still hold, because the invariants (especially "AI never touches the
database") are the product's trust story.

---

## 1. Principles

1. **Invariants are tested, not assumed.** Each non-negotiable rule in `CLAUDE.md` has at least one
   test that fails loudly if it is violated.
2. **Business rules cite tests, tests cite rules.** Test names carry the rule id (`test_BR_W_05_...`).
   A rule without a test is not implemented.
3. **Fast where it matters.** Domain logic is pure and tested in milliseconds. The slow, realistic
   tests are reserved for the places where realism is the point: the database, authorization, and the
   ingestion pipeline.
4. **Real Postgres, not SQLite.** RLS, constraints, `jsonb`, recursive CTEs and pgvector are all part
   of the design. Testing against a different database tests a different system.
5. **AI is tested deterministically.** Model non-determinism is isolated behind the runtime port.
   Pipeline tests use a fake runtime; model quality is measured by evals, which are a separate,
   statistical activity (`docs/ai/ai-architecture.md` §8).

## 2. Test levels

| Level | Scope | Tooling | Speed | Volume |
|---|---|---|---|---|
| L1 Domain unit | Entities, state machines, invariants, derived values. No I/O | pytest | ms | largest |
| L2 Service | Application services against real Postgres, including authz and audit | pytest + testcontainers | 10–100 ms | large |
| L3 Contract | HTTP API against OpenAPI schema; Tool Gateway against tool schemas | pytest + schemathesis | fast | medium |
| L4 Integration | Ingest → outbox → worker → fake agent → tool call → state | pytest + compose services | seconds | focused |
| L5 Frontend | Component and hook tests | Vitest + Testing Library | fast | medium |
| L6 E2E | Critical journeys in a browser | Playwright | slow | small, curated |
| L7 Architecture | Boundaries, imports, migrations, config | pytest + import-linter | fast | small |

Shape: a pyramid at L1–L3, a deliberate bulge at L2 because that is where authorization and audit
live, and a thin, stable L6.

## 3. What gets tested where

### L1 Domain
- Every state transition in `business-rules.md` §4–§8, positive and negative.
- Derived values: severity banding (BR-R-01), staleness (BR-W-10), project health (BR-P-08).
- Dependency cycle detection (BR-D-02), including the returned cycle path.
- Hierarchy depth (BR-W-06), date ordering (BR-P-02), timezone interpretation (BR-G-05).
- Assignment cardinality (BR-W-13): zero or one active OWNER, many CONTRIBUTOR, many REVIEWER; ending
  and re-assigning an OWNER; the single-`is_primary` rule (BR-W-16).
- Property-based tests (Hypothesis) where the input space is interesting: dependency graphs, date
  arithmetic across DST, hierarchy mutations.

### L2 Service
- Each use case: happy path, each rejection path, and the audit entry it must produce.
- Rollup partitioning (BR-RPT-02, BR-RPT-03): seed project and non-project Work for the same person and
  assert project rollups exclude the latter while person and org views include it. This is the rule most
  likely to be quietly broken by a convenient join.
- Optimistic concurrency (BR-G-06): two writers, one loses, nothing is merged.
- Cascades (BR-P-04) and their audit attribution.
- Transactionality: a failure after partial work leaves nothing behind; evidence and entity are
  created atomically (§2 of the domain model).
- Monitor rules MON-001…MON-012: seeded fixtures, frozen clock, asserted findings, and asserted
  idempotence when run twice.

### L3 Contract
- Schemathesis over the generated OpenAPI: no 500s, schema conformance, correct error shapes.
- `Idempotency-Key` and `If-Match` semantics.
- Tool schemas: unknown fields rejected, required fields enforced, structured rejection reasons
  present and stable, and every mutation tool returns a Proposal rather than an applied entity unless
  a valid `approval_record_id` and matching action hash are supplied.
- Frontend client generation drift: regenerating the client must produce no diff.

### L4 Integration
- Ingest the same Event twice: one Event row, one processing run (BR-E-02).
- Full extraction path with the fake runtime producing a scripted tool sequence; assert Proposals,
  evidence, notifications, audit and the AIInteraction record. Assert that **no Work Core state
  changed** at this point (Level 1).
- Approval path: approve a Proposal, assert the ApprovalRecord is written before execution, the
  mutation is attributed to the approver with `executed_via = ai_tool`, and the ApprovalRecord is
  updated with the resulting entity and audit entry (BR-PR-08, traversable in both directions).
- Evidence attachment to an **existing** entity returns a Proposal and persists nothing until approved
  (BR-AI-36); Evidence attached to an entity proposed in the same interaction persists with that
  approval and not before.
- AI Risk narrative: the monitor creates the Risk directly as system actor, the narrative arrives as a
  Proposal, and the Risk text stays empty until approval (BR-AI-37).
- Read-side summary: requesting a summary creates no Proposal, writes no business state, and leaves the
  audit log free of mutations (BR-AI-38).
- No-invention: feed the fake runtime a transcript naming an unknown person and an unknown project;
  assert no Person row, no Project row, no assignment, no synthetic container, and an unresolved
  attribution instead (BR-AI-34, BR-AI-35).
- Authority laundering: an Event whose text says a named person approves something produces no
  ApprovalRecord and no execution.
- Approval tampering: mutate the action between approval and execution, assert `approved_action_hash`
  mismatch rejects it (BR-AI-18). Replay an ApprovalRecord, assert single use (BR-AI-20). Let one
  expire, assert re-approval is required (BR-AI-22).
- **Loop prevention:** create Work through the API, assert a DomainEvent, assert the projected internal
  Event, and assert that no extraction job is enqueued for it (BR-E-11). Run the pipeline for a full
  cycle and assert the Event count does not grow without bound.
- Channel capture: ingest a WhatsApp-shaped Event via the connector principal; assert the connector
  cannot call any Tool Gateway endpoint, and the agent principal cannot call the ingestion API
  (ADR-0027).
- Web capture: post an Event with an attachment, assert MinIO storage, Evidence over the attachment
  with `excerpt = null`, and the full chain to an approved Work item.
- Outbox recovery: kill the worker mid-dispatch, restart, assert exactly-once logical effect.
- Redis loss: flush Redis, assert pending outbox rows re-dispatch and nothing is lost.
- Agent failure modes: malformed output, tool rejection, blast-radius breach, timeout. Each must leave
  the system consistent and the interaction recorded as failed.

### L5 Frontend
- Proposal review flows: accept, edit-and-accept, reject with reason.
- Evidence viewer renders the excerpt and its source attribution.
- AI-originated values are visually attributed (BR-AI-12) — asserted, because this is a trust
  requirement, not styling.
- Optimistic update rollback on conflict.
- Accessibility checks on the core surfaces (axe), keyboard navigability of the review inbox.

### L6 E2E (curated, ~9 journeys)
1. Log in, create a project, create work, assign, complete.
1a. Create Work with **no Project and no assignment**, progress it, complete it. Confirm no UI path
   demands either (ADR-0029, BR-W-15). Then add an OWNER, two CONTRIBUTORs and a REVIEWER, end the
   OWNER assignment, assign a new one, and confirm the history is visible.
1b. Reporting: assert "All Work" splits into Project and Non-project, that a project rollup excludes
   non-project Work, and that an organization-wide count includes both (BR-RPT-01 to BR-RPT-03).
2. **The MVP acceptance journey (PQ-1):** paste a realistic work conversation, see the immutable Event
   with its preserved original, see Proposals with Evidence, edit one and approve it, see the Work or
   Commitment created, and walk the full audit chain Event → Evidence → AIInteraction → Proposal →
   ApprovalRecord → mutation.
3. Reject a proposal; confirm it does not reappear and the rejection reason is recorded.
4. Dependency created, blocker completed, blocked item unblocks.
5. Commitment captured, acknowledged, due date passes, missed notification arrives.
6. Monitor raises a risk; risk appears on the project dashboard with its rule explanation.
7. Executive rollup renders with drill-down into evidence.
8. **AI disabled**: every human path in journey 1 and 4 still works end to end.

### L7 Architecture
These are the tests that protect the design.

| Test | Asserts |
|---|---|
| `test_agent_has_no_db_access` | The agent image/config contains no database DSN; the compose topology places `agent-runtime` outside the data network |
| `test_tools_do_not_import_repositories` | import-linter contract: `app.tools` may import `app.services` only |
| `test_contexts_do_not_cross_import` | Each context imports other contexts only via their public interface module |
| `test_routers_have_no_business_logic` | Routers do not import repositories, sessions or domain mutators |
| `test_every_mutation_writes_audit` | Introspect service registry: each mutating service emits an audit entry (enforced by a decorator whose absence fails the test) |
| `test_every_table_has_org_id` | Schema introspection over every organization-owned table, against the list in BR-G-01 |
| `test_no_autonomy_above_level_2` | The autonomy policy enum has no value above `level_2_approved_execution` (BR-AI-31) |
| `test_project_is_optional_on_work` | `work.project_id` is nullable in the schema, and no API or service path requires it (ADR-0029) |
| `test_no_assignee_column_on_work` | Schema introspection: `work` has no `assignee_person_id`, no owner column and no collaborator array. `WorkAssignment` is the only writable assignment surface (BR-W-12, ADR-0032) |
| `test_single_active_owner_constraint_exists` | A partial unique index enforces one active OWNER per Work; the constraint is asserted at the database level, not just exercised through the service (BR-W-13) |
| `test_no_synthetic_projects_in_seed_or_migration` | No migration, seed or fixture creates a General/Default/Unassigned/Inbox Project (BR-P-07a) |
| `test_no_comment_table` | Schema introspection: no `comment` table exists; `COMMENT` is an Event type (ADR-0033) |
| `test_internal_events_never_enqueued` | Static and runtime check that the extraction dispatcher filters on `origin = external` (BR-E-11) |
| `test_rls_policies_present` | Every org-scoped table has an RLS policy enabled |
| `test_migrations_apply_and_rollback` | Alembic up/down on a clean database; no drift versus models |
| `test_openapi_snapshot` | API surface changes are intentional and reviewed |

## 4. Test data

- Factories per aggregate (`factory_boy` or plain builders), producing valid-by-construction entities.
- A named seed scenario, "Northwind Org": 1 organization, 3 teams, 2 projects, ~40 work items, a
  dependency chain, an open risk, several commitments and a dozen events. Used by L4, L6, evals and
  local development so everyone debugs against the same world.
- Fixed clock in all tests. Time is an input, never ambient.
- No production data in any environment, ever, including for eval development.

## 5. Authorization testing

Treated as its own discipline because authorization bugs are silent and severe.

A generated matrix over `role × scope-relationship × resource-type × action × visibility`, asserting
allow or deny for every cell. Organization is a dimension of every cell, not an afterthought. The matrix is data, in one file, reviewed like a spec. Adding a role, a
resource type or an action fails the build until the new cells are declared.

**Tenant isolation suite (PQ-2 acceptance criteria).** A dedicated suite seeds two organizations with
deliberately similar data and asserts that a user of Organization A cannot:
- read Organization B entities by id, by list, by filter or by search;
- modify them, including through a proposal-approval path;
- drive an AI action against them: an AIInteraction bound to org A rejects tool calls naming org B
  targets, at the Tool Gateway and again at RLS;
- infer them through counts, rollups, aggregate insights, pagination totals, autocomplete, error
  messages or timing-visible differences between "absent" and "forbidden".

The suite runs with RLS enabled **and** in a second pass with application scoping deliberately removed,
to prove RLS alone still holds. If that second pass ever passes only because of application code, the
defence in depth is theatre.

Plus targeted cases:
- Cross-organization access returns 404, not 403 (existence is itself information).
- AI delegated permissions never exceed the principal's (BR-AI-03), tested by giving the agent a
  delegated principal weaker than the agent role and asserting the intersection across all four terms
  (agent role, delegated authority, capability policy, org scope).
- Approving does not grant authority: an approver without rights on the target fails at execution
  (BR-AI-21, BR-PR-05).
- No AI code path reaches a mutation without an ApprovalRecord: enumerate every mutation tool and
  assert each rejects a call lacking one (BR-AI-16).
- Notification delivery re-checks authorization (BR-N-03): revoke access after creation, assert
  non-delivery.
- Restricted-sensitivity Events are excluded from AI processing (BR-AI-13) and from non-participant
  reads.

## 6. AI testing versus AI evaluation

| | Testing | Evaluation |
|---|---|---|
| Question | Does the machinery behave correctly? | Is the model's output good? |
| Determinism | Required | Statistical |
| Model | Fake runtime or cached responses | Real |
| Gate | Pass/fail, blocks merge | Thresholds, blocks merge or promotion |
| Owner | Engineering | Engineering + product |
| Location | `backend/tests/` | `agent/evals/` |

Both run in CI. Evals are defined in `docs/ai/ai-architecture.md` §8; they are referenced here so that
nobody concludes that a green test suite means the AI is working.

## 7. Non-functional testing

- **Performance:** seed 50k work items and 100k events; assert p95 on list endpoints, rollup queries,
  dependency-graph traversal and monitor runs. Query plans reviewed for the top ten queries.
- **Load:** ingestion burst (a week of events in a minute) to validate back-pressure and queue depth.
- **Resilience:** dependency-down drills for model provider, Redis, MinIO (§11 of the architecture
  doc), asserting documented degradation rather than failure.
- **Security:** dependency scanning, container scanning, secret scanning in CI; the adversarial eval
  set as an injection regression suite; an authorization fuzz pass over the API.

## 8. CI pipeline

```
lint + typecheck  →  L1  →  L7  →  L2  →  L3  →  L5      (on every push)
                          ↓
                    L4 + L6 + eval (cached)               (on PR)
                          ↓
                    nightly: full evals (live model), performance, security scans
```

Merge blockers: any failing test, coverage regression on `contexts/` (target ≥ 85% for domain and
services; coverage is not tracked as a goal elsewhere), missing authorization matrix cells, OpenAPI
drift, migration drift, eval gate breach on a changed capability.

## 9. Open testing questions

| # | Question | Options |
|---|---|---|
| T-1 | Compose-based L4 in CI, or testcontainers only? | (a) testcontainers for Postgres/Redis/MinIO, fake Keycloak (proposed, faster); (b) full compose, slower but closer to pilot |
| T-2 | How much live-model testing is affordable per PR? | (a) cached responses plus a 10-case live sample (proposed); (b) fully cached, live only nightly |
| ~~T-3~~ | **RESOLVED (Checkpoint 4a): option (a).** Tests sign their own tokens with an RSA key generated in a fixture and stub the JWKS document; Keycloak is never started by the suite. What is stubbed is key *distribution* only — `TokenVerifier` runs its real checks, so expired, forged, mis-audienced and mis-issued tokens are rejected in tests by the same code that rejects them in production, and each of those four has its own case. `ops/keycloak/realm.json` exports a dev realm for running the stack by hand. | — |
| T-4 | Do we need contract tests between frontend and backend beyond generated clients? | Likely not in v1 given a single first-party client |
| T-5 | How do we test the OpenClaw channel adapter without a live WhatsApp connection? | (a) a recorded-fixture adapter replaying realistic message shapes (proposed); (b) a sandbox number, which adds a live dependency to CI |
| T-6 | Does the eval corpus need native-speaker review for Vietnamese labelling (N-2)? | Likely yes; inter-annotator agreement is meaningless without it |
