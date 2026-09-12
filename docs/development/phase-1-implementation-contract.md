# Phase 1 Implementation Contract

Issued by: Product Owner / Solution Architect · Recorded into the repository: Checkpoint 4a
Status: **binding for all of Phase 1**

This document was issued as a chat instruction and existed only outside the repository until now.
Code comments and checkpoint reports cite it as "contract §n"; those citations pointed at nothing,
which is worse than no citation at all. It is recorded here verbatim in structure so the section
numbers referenced across the codebase resolve.

Sits alongside `docs/decisions/decision-pack-v1.0.md` and `resolution-pack-v1.1.md`. Where this
contract and a decision pack differ, the decision pack governs *what* to build and this contract
governs *how*.

---

## 1. Implementation order

1. Repository/runtime foundation
2. PostgreSQL extensions and platform migration
3. Identity and organization schema
4. Authorization model and authorization matrix
5. RLS and tenant isolation
6. Audit infrastructure
7. Outbox infrastructure
8. Project
9. Milestone
10. Work
11. WorkAssignment
12. Dependency
13. Read models
14. Application services
15. API layer
16. Frontend
17. Integration/E2E tests

Do not start with CRUD endpoints. Do not build UI before the domain and application authorization
model is established.

## 2. First implementation checkpoint

Migrations 0001 and 0002, authorization model skeleton, authorization matrix, RLS infrastructure,
audit infrastructure, outbox infrastructure, architecture tests, unit tests for authorization. No
Project/Work CRUD. Successful only when migrations apply and roll back, tenant isolation passes,
authorization passes, audit passes, architecture/import tests pass, and CI is green. Then stop and
report.

## 3. Authorization is a first-class domain boundary

Not scattered checks inside routers. A centralized authorization/application-policy mechanism:

```
Request → authenticated principal → organization membership → role
        → resource relationship → action → authorization decision → application service
```

Routers must not contain business authorization logic. Application services must not trust the
router to have performed authorization. Sensitive mutations are authorized at the
application-service boundary.

## 4. Tenant isolation

Enforced at two levels.

**Application** — every request resolves User → Organization Membership → active organization
context → authorized scope.

**PostgreSQL** — RLS enforces organization boundaries. The RLS tests must demonstrate isolation even
when application-level scoping is intentionally bypassed. Application filtering is not a substitute
for RLS.

## 5. No cross-tenant inference

Isolation applies beyond direct CRUD. Tests must cover direct reads, list queries, search,
aggregation, counts, project rollups, work rollups, authorization checks, audit queries, and future
AI/tool boundaries. A user from Organization A must not be able to infer Organization B data through
counts, summaries, error messages, or relationship traversal.

## 6. Work model

Work + optional Project + WorkAssignment + Dependency.

`work.project_id` is nullable. Work can exist without a Project. Work can exist without an
assignment. `WorkAssignment` is the canonical assignment source. No `work.assignee_person_id`.
Maximum one active OWNER. Multiple CONTRIBUTOR and REVIEWER assignments allowed. No
synthetic/default/general Project.

## 7. WorkAssignment

First-class entity. MVP roles OWNER, CONTRIBUTOR, REVIEWER. Enforced at database and application
level: maximum one active OWNER per Work; assignment belongs to the same Organization as the Work and
the Person; assignment cannot cross a tenant boundary; ending an assignment is auditable;
reassignment is auditable. No mutable duplicate assignment field on Work. The `work_current_owner`
view may be used for read convenience.

## 8. Dependency

Dependencies must not permit cycles; `A → B → C → A` is rejected. Cycle protection is not deferred to
the UI. The invariant exists in the documented domain/application/database strategy. Tests cover
direct cycle, indirect cycle, self-dependency, cross-tenant dependency, and valid chains.

## 9. Audit

Every important mutation produces an audit record carrying at minimum actor, organization, action,
resource type, resource id, timestamp, before/after or equivalent change information where
appropriate, and a correlation/request id. Business services do not silently mutate important state
without audit. Audit records are protected from ordinary mutation.

## 10. Outbox

Transactional outbox. A business mutation and its outbox event commit atomically. No domain event is
published to an external transport before the transaction commits. Outbox processing may be
asynchronous. The outbox is infrastructure; it must not turn Phase 1 into event sourcing. The system
remains a normal transactional Work Core.

## 11. Domain boundaries

The seven documented bounded contexts: Identity & Organization, Work Core, Commitment &
Accountability, Governance, Signal, Intelligence, Notification. Phase 1 implements only what Work
Core and the platform foundations require. No fake implementations of future contexts merely to
satisfy architecture diagrams. Import-linter rules prevent prohibited dependencies.

## 12. API

REST + OpenAPI. No GraphQL. Routers are thin.

A router parses the request, authenticates, invokes an application service, and serializes the
response. A router must not contain business rules, execute SQL directly, implement authorization
policy, or mutate multiple aggregates manually.

## 13. Database access

Business code does not execute arbitrary SQL outside the approved repository/data-access boundary.
LLM/agent code has no database credentials. The AI architecture stays physically and logically
separated from PostgreSQL. Phase 1 already preserves the future invariant:

```
AI → Tool Gateway → Application Service → Domain → Database
```

never `AI → Database`.

## 14. Frontend

The frontend consumes the API. It does not duplicate business authorization rules; frontend
permissions are UX guidance only and the backend remains authoritative. No visually elaborate UI in
Phase 1. Priorities: correct data model, clear Work/Project screens, organization context,
authorization-aware navigation, error handling, testability.

## 15. Testing requirements

**Unit** — domain rules, authorization, WorkAssignment rules, dependency rules.
**Integration** — PostgreSQL, migrations, RLS, audit, outbox.
**Architecture** — import boundaries, router purity, organization scoping, no assignee mirror, no
synthetic Project, AI/database separation contract.
**E2E**, at minimum: create organization; create member; create project; create Work without Project;
create Work with Project; assign OWNER; assign CONTRIBUTOR; reassign OWNER; create dependency; reject
dependency cycle; verify audit; verify tenant isolation.

## 16. Migration discipline

Every schema change is an explicit migration. Never modify an already-applied migration to fix a
later problem. Never use destructive schema changes without an explicit migration and review.
Migration names remain sequential. CI tests clean-database migration, migration from the previous
version, rollback where supported, and schema consistency.

## 17. No premature optimization

Do not introduce Kubernetes, microservices, Kafka, event sourcing, GraphQL, distributed transactions,
service mesh or complex CQRS unless an explicit ADR authorizes them. The architecture is a modular
monolith using FastAPI, PostgreSQL, Redis, Keycloak, MinIO and Docker Compose.

## 18. No speculative features

No AI extraction, OpenClaw integration, WhatsApp adapter, Zalo, Gmail, Calendar, autonomous agents,
AI Q&A, Risk AI or Commitment AI during Phase 1. Build the Work Core foundation correctly first.

## 19. Definition of Done

A Phase 1 feature is not done because code compiles, an endpoint returns 200, the UI renders, or
basic CRUD works. It is done when domain rules are implemented, authorization is implemented, tenant
isolation is verified, audit is implemented, tests pass, a migration exists, OpenAPI is updated,
documentation is updated, architecture boundaries pass, there is no known security regression, and no
TODO hides an unresolved business rule.

## 20. Required reporting after each checkpoint

Files changed · Database (migrations, tables, indexes, constraints, RLS policies, views) · API
(endpoints added/changed) · Authorization (rules added) · Tests (total, passed, failed, skipped) ·
Architecture · Security (tenant isolation) · Known issues · Deviations (anything differing from this
contract or the approved ADRs) · Next step.

Do not automatically continue to the next checkpoint. Stop and wait for approval.

## Final instruction

Implement only the current checkpoint. Do not anticipate later phases with speculative code. Prefer
the simplest implementation that satisfies the approved architecture and business rules. If a
requirement is ambiguous: stop, do not invent a business rule, report the ambiguity and wait for a
decision.
