# Definition of Done — AI WorkOS

Version 0.1 · Status: proposed

"Done" means a reviewer can merge without asking follow-up questions and an operator can run it
without surprises. Use the checklist that matches the change.

---

## 1. Universal (every change)

- [ ] The change is within the current phase in `progress.md`, or the phase entry was updated first.
- [ ] Business rules affected are written or updated in `docs/domain/business-rules.md` **before** the
      code, with rule ids.
- [ ] Tests exist at the right level and cite rule ids where applicable.
- [ ] `ruff`, `mypy --strict` on `contexts/` and `tools/`, ESLint and `tsc --noEmit` all clean.
- [ ] No new import-boundary violations (L7 tests pass).
- [ ] No secrets, tokens, real names or production data in code, tests, fixtures or logs.
- [ ] `docs/development/progress.md` updated.

## 2. Backend feature

- [ ] Domain logic lives in the domain layer; the service orchestrates; the router only translates.
- [ ] Authorization check performed in the application service, via `platform/authz`, not ad hoc.
- [ ] Authorization matrix updated with every new `(role, resource, action)` cell; tests pass.
- [ ] Audit entry emitted in the same transaction as the mutation (BR-G-02).
- [ ] Domain event written to the outbox where other contexts need to react (ADR-0005).
- [ ] Optimistic concurrency honoured on mutable entities (BR-G-06).
- [ ] `org_id` present, scoped in queries, and covered by an RLS policy; tenant isolation suite extended
      to cover the new resource (PQ-2 acceptance criteria).
- [ ] Errors return `problem+json` with a stable type URI.
- [ ] Migration written, applies and rolls back cleanly, no drift versus models, no destructive
      operation without an explicit note in the PR.
- [ ] Indexes considered for new query paths; plan checked if the table is expected to grow.
- [ ] OpenAPI regenerated; frontend client regenerated; no unreviewed drift.

## 3. Frontend feature

- [ ] Server state through TanStack Query; no duplicated client-side copies of server truth.
- [ ] Loading, empty, error and permission-denied states all handled explicitly.
- [ ] AI-originated content is visually attributed and one click from its evidence (BR-AI-12).
- [ ] Works when `ai_enabled = false`: no dead surfaces, no broken layout, no errors.
- [ ] Keyboard navigable; axe checks pass on the touched surfaces; focus order sensible.
- [ ] Component tests cover the interaction, not just the render.
- [ ] No new global state, no new dependency without a note in the PR.

## 4. AI / tool change

Additional, mandatory:

- [ ] Tool schema versioned; manifest version bumped; old version still resolvable for in-flight
      interactions.
- [ ] Tool enforces authorization, validation, evidence requirement, idempotency and blast radius.
      None of these may be "handled upstream".
- [ ] Prompt change is a new version file; nothing references "latest".
- [ ] Eval cases added or updated for the changed behaviour, including at least one negative case.
- [ ] Offline eval run attached to the PR; gates in `docs/ai/ai-architecture.md` §8.2 met.
- [ ] Adversarial subset run; no new injection regressions.
- [ ] Cost and latency impact measured and stated.
- [ ] Autonomy level for the affected capability is explicit and justified, and any promotion cites
      the online metrics that qualify it.
- [ ] The change cannot create, delete or close anything forbidden by BR-AI-06, BR-AI-08 or BR-AI-23
      through BR-AI-27.
- [ ] Autonomy for the capability is at most `level_2_approved_execution`. No new schema value, config
      key or code path can express anything higher (BR-AI-31).
- [ ] Every mutation the change introduces requires a matching ApprovalRecord, with hash verification,
      single use and expiry tested (BR-AI-18, BR-AI-20, BR-AI-22).
- [ ] The full chain Event → Evidence → AIInteraction → Proposal → ApprovalRecord → mutation → audit is
      traversable in both directions for anything the change can produce (BR-PR-08).
- [ ] Any new Event type is classified `external` or `internal`, and internal types are excluded from
      extraction (BR-E-11).
- [ ] Nothing AI-generated is persisted without a Proposal and approval, including Evidence links on
      existing entities and Risk narratives (BR-AI-36, BR-AI-37).
- [ ] If the change persists a computed or summarised value anywhere, it is either a derived,
      invalidatable cache nobody reads as business truth, or it goes through Proposal/Approval
      (BR-AI-38). State which, in the PR.
- [ ] The change cannot invent a Person or a Project membership under any input (BR-AI-34, BR-AI-35).

## 4a. Work Core change touching Project or assignment

- [ ] Project remains optional: the change introduces no required `project_id`, no UI step that forces
      Project selection, and no default, inferred or synthetic Project (ADR-0029, BR-W-07, BR-W-07a,
      BR-P-07a).
- [ ] Assignment remains optional: Work can be created, progressed and completed with no assignment
      (BR-W-15).
- [ ] `WorkAssignment` stays canonical: no new writable owner or assignee column anywhere, and any new
      fast-access path is a view, projection or invalidatable cache (BR-W-12, ADR-0032).
- [ ] The single-active-OWNER constraint is enforced in the schema, not only in code (BR-W-13).
- [ ] Any new count, list or rollup states which partition it covers, and project rollups exclude
      non-project Work (BR-RPT-01 to BR-RPT-04).

## 5. Ingestion / connector change

- [ ] Idempotency on `(org_id, source_system, source_ref)` verified with a duplicate-delivery test.
- [ ] Sensitivity classification applied; default is the more restrictive option.
- [ ] Raw payload stored with retention metadata; `body_text` normalised and usable for verbatim
      excerpts (BR-E-05).
- [ ] Participant resolution produces confidences and never auto-attributes below threshold
      (BR-I-06).
- [ ] Rate limits, payload size limits and back-pressure behaviour tested.
- [ ] Connector credentials are per-source, revocable, and documented in `ops/`.
- [ ] The connector principal holds ingestion rights only and provably cannot call the Tool Gateway
      (ADR-0027).
- [ ] Sender policy applied: unknown senders are captured per the configured channel policy and are
      never auto-attributed as committers (BR-I-06, BR-E-12, S-7).
- [ ] Adding the connector required no change to the Work Core, the Tool Gateway or the domain model.
      If it did, that is a boundary defect, and it is reported rather than worked around.

## 6. Architectural change

- [ ] ADR written, numbered, in `docs/decisions/`, with context, options, decision, consequences.
- [ ] `ADR-INDEX.md` updated; superseded ADRs marked, not edited.
- [ ] Affected architecture, domain, security or AI documents updated in the same change.
- [ ] Migration or compatibility path described for anything already deployed.

## 7. Release / phase completion

- [ ] All acceptance criteria for the phase in `progress.md` met and demonstrated.
- [ ] Full test suite green, including L4, L6 and nightly evals.
- [ ] Compose stack starts clean from an empty volume set and seeds successfully.
- [ ] Backup and restore rehearsed for Postgres and MinIO.
- [ ] Dashboards and alerts exist for the new surface area.
- [ ] Open questions resolved or explicitly re-deferred with an owner and a date.
- [ ] Someone who did not build it has run through the phase demo unaided.

## 8. Explicitly not required

To avoid ceremony that buys nothing at this stage: no formal sign-off documents, no test plans as
prose, no coverage targets outside `contexts/`, no UML deliverables, no performance budget on surfaces
with fewer than ten users.
