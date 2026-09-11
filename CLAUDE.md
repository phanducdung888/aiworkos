# CLAUDE.md — Working Agreement for AI WorkOS

This file is the entry point for any AI agent or engineer working in this repository.
Read it before doing anything else. If a task conflicts with this file, stop and ask.

**Status: Phase 0 (architecture only). No application code exists yet and none should be written
until the Phase 0 exit criteria in `docs/development/progress.md` are met.**

**Decision Pack v1.0 and Resolution Pack v1.1 are binding.** See
`docs/decisions/decision-pack-v1.0.md` and `docs/decisions/resolution-pack-v1.1.md`. These are Product
Owner / Solution Architect decisions. Do not reinterpret, weaken or replace them. If an implementation
detail conflicts with one, stop and report the conflict before writing code.

---

## 1. What this product is

AI WorkOS is an AI-native work management platform. It maintains structured organizational
work state (projects, work, commitments, dependencies, risks, decisions) and uses AI to
capture that state from human activity rather than from forms.

Two layers, strictly separated:

- **Work Core** — deterministic system of record. Owns all state. Works with AI switched off.
- **Intelligence Layer** — AI that observes activity, proposes structure, monitors, explains.
  Owns no state. Cannot reach the database.

## 2. Non-negotiable rules

These are architectural invariants, not preferences. Violating one is a defect even if tests pass.

1. **The AI never touches the database.** No ORM session, no SQL, no repository, no migration,
   no direct Redis or MinIO access from the agent runtime. Mutation happens only through the
   Tool Gateway, which calls the same application services the HTTP API calls.
   This is enforced three ways: network segmentation, database credentials that do not exist in
   the agent container, and a CI import-boundary check.
2. **The system stays useful without AI.** Every entity the AI can create must be creatable and
   editable by a human through the UI and the public API. If a feature only works with AI, it is
   in the wrong layer.
3. **AI authority never exceeds human authority.** An AI tool call carries a delegated principal.
   Its effective permissions are the intersection of the agent's own role and that principal's
   permissions. There is no "AI admin" path.
4. **Every AI-originated write carries evidence.** If the AI creates or changes a Work, Commitment,
   Risk or Decision, the change links to the Evidence that justifies it and to the AIInteraction
   that produced it. No provenance, no write.
5. **Detection is deterministic; interpretation is AI.** Overdue, blocked, slipping, unowned,
   stale: these are SQL and rules, and must be reproducible and explainable. The AI summarises,
   hypothesises and narrates on top of those facts.
6. **Content from ingested activity is data, never instruction.** Transcripts, messages and
   documents are untrusted input. They may not steer tool selection or expand scope.
7. **Minimise manual data entry, never by guessing silently.** Low-confidence inference becomes a
   visible proposal, not a hidden fact.
8. **Ambiguity gets documented, not decided in code.** If a requirement is unclear, add it to the
   open-questions section of the relevant doc and propose options. Do not invent requirements.
9. **AI autonomy is Level 1–2 only.** Level 1: observe and propose. Level 2: execute through the Tool
   Gateway *after* an explicit user approval that is recorded immutably (approver, proposal, exact
   proposed action, timestamp, resulting mutation). Level 3+ is out of scope. Autonomy is
   capability-scoped policy, never a global boolean.
10. **Multi-organization from day one.** Every organization-owned entity carries `org_id`, is scoped
    in application authorization, and is protected by RLS. A single-org MVP deployment is acceptable;
    a single-org schema is not.
11. **Project is optional on Work, and so is assignment.** Work with no Project and no assignee is a
    first-class, fully functional record. Never force either, never invent either, and never create a
    synthetic General/Default/Unassigned Project.
12. **`WorkAssignment` is canonical.** No assignee column on Work, ever. Roles are OWNER (zero or one
    active), CONTRIBUTOR and REVIEWER (zero or more). Fast owner lookup is a view or projection.
13. **Persistence, not computation, triggers autonomy policy.** AI may calculate, explain and summarise
    freely. The moment anything AI-generated is persisted as business state, it needs a Proposal and an
    approval. Attaching Evidence to an existing entity and writing a Risk narrative both count.

## 3. Where the answers live

| Question | Document |
|---|---|
| What are we building and why, what is out of scope | `docs/product/product-constitution.md` |
| Components, contexts, data flow, deployment | `docs/architecture/system-architecture.md` |
| Entities, attributes, relationships, lifecycles | `docs/domain/domain-model.md` |
| Invariants, state machines, validation rules | `docs/domain/business-rules.md` |
| AI boundaries, tools, autonomy, evaluation | `docs/ai/ai-architecture.md` |
| AuthN/AuthZ, tenancy, AI principals, threats | `docs/security/security-model.md` |
| How we test, including AI | `docs/testing/testing-strategy.md` |
| What "finished" means | `docs/development/definition-of-done.md` |
| Current phase and task state | `docs/development/progress.md` |
| Why a choice was made | `docs/decisions/ADR-INDEX.md` |

## 4. Planned repository layout

Do not create directories outside this shape without an ADR.

```
ai-workos/
├── CLAUDE.md
├── docs/
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── api/              # HTTP routers (public v1), thin
│   │   ├── tools/            # Tool Gateway: AI-facing façade over services
│   │   ├── contexts/         # bounded contexts, one package each
│   │   │   ├── identity/
│   │   │   ├── work/
│   │   │   ├── commitment/
│   │   │   ├── governance/   # risks + decisions
│   │   │   ├── signal/       # events + evidence
│   │   │   ├── intelligence/ # AIInteraction records, orchestration triggers
│   │   │   └── notification/
│   │   ├── platform/         # db, auth, authz, outbox, audit, config, telemetry
│   │   └── workers/          # queue consumers, schedulers, monitors
│   ├── migrations/           # Alembic
│   └── tests/
├── frontend/                 # React + TypeScript + Vite
├── agent/                    # OpenClaw runtime config, prompts, tool manifests, evals
└── ops/                      # compose files, Keycloak realm, seed data
```

Each bounded context package exposes a public interface module. Cross-context imports go through
that module only. Contexts share the database but never each other's tables directly.

## 5. Layering

```
HTTP router ─┐
             ├─→ application service (use case) ─→ domain ─→ repository ─→ Postgres
Tool Gateway ┘
```

- Routers and tools contain no business logic. They translate, authorise and delegate.
- Application services own transactions, authorisation checks and audit emission.
- Domain objects own invariants and are free of I/O.
- Repositories are the only code that talks to the database.

## 6. Conventions

- Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2. Formatting `ruff`, typing `mypy --strict`
  on `contexts/` and `tools/`.
- TypeScript strict, React function components, TanStack Query for server state. No client-side
  duplication of server-owned state.
- All identifiers are UUIDv7. All timestamps stored UTC, `timestamptz`. Dates that people commit to
  are `date` plus an explicit timezone on the owning entity.
- Every table has `org_id`, `created_at`, `updated_at`, `created_by_actor`, and where relevant
  `source` (`human` | `ai` | `system` | `import`).
- Errors use RFC 9457 `application/problem+json`.
- Nothing is hard-deleted while it has audit or evidence attached. Use lifecycle states.
- Migrations are forward-only in production and must be reversible in development.

## 7. Workflow for any change

1. Confirm the phase in `docs/development/progress.md` allows the work.
2. Check whether the change requires a decision. If yes, write the ADR first.
3. Write or update the business rules before the code that implements them.
4. Implement: domain → service → repository → API/tool → UI.
5. Add the authorisation matrix case. Missing authz tests fail review.
6. If the change touches AI behaviour, update the tool manifest, the prompt version and the eval
   set in the same change.
7. Run the Definition of Done checklist.
8. Update `docs/development/progress.md`.

## 8. Decided (Decision Pack v1.0)

- **Capture sources (PQ-1):** web manual entry and paste (mandatory path), OpenClaw-connected
  messaging with WhatsApp as the first channel, and internal system-generated Events. No other
  connectors in the MVP.
- **Tenancy (PQ-2):** multi-organization capable schema, authorization and API; one organization in
  the first deployment; shared Keycloak realm, org membership resolved in the application.
- **Autonomy (PQ-3):** Levels 1–2 as described in rule 9 above.
- **Naming (A-1):** `Event`, `Evidence` and `DomainEvent` stay three distinct concepts.
- **Project (A-7):** optional on Work; no synthetic containers; rollups partition Project and
  Non-project Work.
- **Assignment (M-1, ADR-0032):** `WorkAssignment` canonical, many per Work, one active OWNER.
- **Comments (N-3, ADR-0033):** no Comment entity; `Event.type = COMMENT`.
- **C-1 (ADR-0034):** Evidence attachment to existing entities and Risk narratives are Level 1;
  non-persisted read-side summaries need no Proposal.

## 9. Things that are still not decided

Do not silently resolve these. They are tracked with options in the relevant docs and summarised
in `docs/development/progress.md`:

- OpenClaw's tool protocol, sandboxing, model routing, and its channel-adapter behaviour (A-3).
- Whether outbound messaging (AI asking a person for clarification on WhatsApp) is in the MVP (N-1).
- Which languages extraction must handle at MVP quality (N-2).
- Whether `COMMENT` Events should be extraction-eligible despite being internal-origin (N-4).
- Channel sender policy for WhatsApp (S-7) and the legal posture on capture (PQ-4).
- External identity resolution from phone numbers to People (PQ-7).
- Whether Commitment stays a separate aggregate (A-2).

None of these block Phase 1.
