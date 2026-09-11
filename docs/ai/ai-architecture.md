# AI Architecture — AI WorkOS

Version 0.1 · Status: proposed

How the intelligence layer is bounded, what it may do, how it does it, and how we know whether it
works. The governing constraint is that the AI is a *participant* in the system with an identity,
permissions and an audit trail, not a privileged internal component.

---

## 1. The boundary, stated precisely

```
        ┌───────────────────────── TRUST BOUNDARY ─────────────────────────┐
        │                                                                  │
        │   Agent Runtime (OpenClaw)                                       │
        │   · prompts, model calls, reasoning, retries                     │
        │   · holds: model API key, its own service credentials            │
        │   · holds NOT: DB credentials, Redis, MinIO, admin tokens        │
        │   · network: API internal tool port + allow-listed model egress  │
        │                                                                  │
        └───────────────┬──────────────────────────────────────────────────┘
                        │  HTTPS, mTLS or signed service token
                        │  every call: agent identity + delegated principal
                        │             + ai_interaction_id + idempotency key
        ┌───────────────▼──────────────────────────────────────────────────┐
        │   Tool Gateway  (inside the API service)                         │
        │   1. authenticate agent                                          │
        │   2. resolve delegated principal, compute effective permissions  │
        │   3. validate arguments against the tool's JSON schema           │
        │   4. check tool is on the manifest version's allow-list          │
        │   5. enforce blast radius + rate limit for this interaction      │
        │   6. enforce evidence requirement for mutations                  │
        │   7. call the application service (same one the UI calls)        │
        │   8. record ToolCall + AuditEntry, success or rejection          │
        └───────────────┬──────────────────────────────────────────────────┘
                        ▼
                Application Services → Domain → Repositories → PostgreSQL
```

Three independent controls enforce "AI never modifies the database" (ADR-0002):

1. **Topology** — the agent container is not on the data network.
2. **Credentials** — no database DSN is mounted into the agent image; the database role used by the
   API is not issued to any agent identity.
3. **Code** — CI import-boundary tests fail if `agent/` or `app/tools/` imports a repository, a
   session factory or a model class directly.

Any one of these failing still leaves two. That redundancy is deliberate: this is the rule most
likely to be eroded by a convenient shortcut.

## 2. What the AI is for, and what it is not for

| AI does | AI does not |
|---|---|
| Extract candidate Work, Commitments, Dependencies, Risks, Decisions from activity | Decide that a milestone is at risk (monitors do that) |
| Link new information to existing entities rather than duplicating | Delete, cancel or close anything |
| Normalise vague language into structured fields with confidence | Create people, teams, departments, projects or milestones |
| Draft explanations, summaries and status narratives over computed facts | Invent numbers not present in its input |
| Answer questions over the work graph, with citations | Answer without citations |
| Suggest mitigations for detected risks | Accept a decision, or mark a commitment fulfilled |

The split follows P5 in the constitution: **detection is deterministic, interpretation is AI**. This
is not caution for its own sake. Deterministic detection is testable, cheap, reproducible and
defensible in a conversation with an executive; model-based detection is none of those.

## 3. AI capabilities (v1)

| Capability | Trigger | Inputs | Outputs | Autonomy (PQ-3) |
|---|---|---|---|---|
| **C1 Extraction** | new **external** Event normalised | Event body, participants, candidate entities in scope | Work / Commitment / Risk / Decision Proposals + Evidence | Level 1, then Level 2 on approval |
| **C2 Linking & dedup** | after C1, or on entity creation | candidate + similar entities | link-to-existing or new-entity Proposal | Level 1, then Level 2 on approval |
| **C3 Progress maintenance** | new external Event referencing a known entity | Event, entity current state | status/date/description update Proposal + Evidence | Level 1, then Level 2 on approval |
| **C4 Risk explanation** | deterministic finding raised | finding, related entities | narrative, suggested mitigation, suggested owner | Level 1 (see C-1 below) |
| **C5 Summarisation** | schedule or user request | computed rollup numbers + entity references | status narrative, not persisted on business entities | Level 1 / read-side only (see C-1) |
| **C6 Question answering** | user request | question + scoped read tools | grounded answer with citations | read-only |

Anything beyond these six is out of scope for v1 and requires an ADR.

## 4. Tool catalogue

Tools are the AI's entire vocabulary. They are intention-shaped, narrow, versioned and individually
authorised. A tool that could express "update any field on any entity" would defeat the design, so no
such tool exists.

### 4.1 Read tools

| Tool | Purpose | Notes |
|---|---|---|
| `get_event(event_id)` | Fetch normalised Event content | Subject to sensitivity policy (BR-E-08, BR-AI-13) |
| `search_people(query, scope)` | Resolve a name or handle to a Person | Returns confidence; never auto-confirms (BR-I-06) |
| `find_similar_work(text, project_id?, limit)` | Candidate existing Work | Hybrid: lexical + pgvector (ADR-0017) |
| `find_similar_commitments(text, person_id?)` | Candidate existing Commitments | |
| `find_similar_risks(text, scope)` | Candidate existing Risks | |
| `find_similar_decisions(text, scope)` | Candidate existing Decisions | |
| `get_entity(type, id)` | Current state of one entity | Authorised per entity |
| `list_project_context(project_id)` | Project, milestones, open work summary | Bounded page size |
| `get_finding(finding_id)` | Deterministic monitor output | For C4 |
| `get_rollup(scope_type, scope_id, period)` | Computed numbers for summarisation | The only numeric source for C5 |

### 4.2 Mutation tools

Each takes `ai_interaction_id`, `evidence[]` and `idempotency_key`. Under Level 1 every one of these
returns a **Proposal**, never an applied entity. The same tools are re-invoked at Level 2 by the
approval path, carrying an `approval_record_id` and the `approved_action_hash`; the gateway refuses to
execute if the hash does not match the action as approved (BR-AI-18, BR-AI-20).

| Tool | Creates / changes | Hard constraints |
|---|---|---|
| `propose_work(...)` | Work | requires prior `find_similar_work` in the same interaction (BR-AI-05); `project_id` is optional and must be omitted rather than guessed (BR-W-07a, BR-AI-35); assignment is a separate proposal, never a field on this call |
| `update_work_progress(work_id, status?, due_date?, note)` | Work status/date | cannot set `cancelled`; cannot reassign |
| `record_commitment(...)` | Commitment | requires committer resolution above threshold; on a channel-captured Event the committer may be an unresolved `sender_external_id`, in which case the Proposal carries the raw identifier and asks the reviewer to resolve it |
| `propose_assignment(work_id, person_id, role)` | WorkAssignment | `role` is `OWNER`, `CONTRIBUTOR` or `REVIEWER`. Never executed without approval. If the person cannot be resolved above threshold, the tool must not be called with a guessed `person_id` (BR-AI-34) |
| `propose_unresolved_attribution(work_id, raw_identifier, context)` | Proposal only | The explicit "someone said this, I don't know who" outcome. It exists so the model always has a correct action available and is never cornered into guessing |
| `update_commitment(commitment_id, status?, due_date?)` | Commitment | may only set `fulfilled` via an ApprovalRecord naming that action (BR-C-10) |
| `link_dependency(blocker, blocked, kind, rationale)` | Dependency | cycle check server-side (BR-D-02) |
| `raise_risk(...)` | Risk | proposals only unless attached to a finding (BR-R-08) |
| `update_risk_narrative(risk_id, description, mitigation, suggested_owner?)` | Risk text | cannot change status. Returns a Proposal; the narrative persists only on approval (BR-AI-37, C-1b) |
| `propose_decision(...)` | Decision | never `accepted` (BR-DE-06) |
| `link_entities(source, target, relation)` | Evidence/link | for dedup and fulfilment links |
| `attach_evidence(target, event_id, locator, excerpt, assertion, confidence)` | Evidence | excerpt verified verbatim server-side (BR-E-05). Against an **existing** entity this returns a Proposal, not a persisted link (BR-AI-36). Against an entity proposed in the same interaction it travels with that Proposal and persists on approval |
| `record_no_signal(event_id, reason)` | AIInteraction annotation | the explicit "nothing here" outcome, used in evals |

Tools that will never exist: raw query execution, arbitrary field update, bulk operations, user or
permission management, role or policy changes, deletion of anything, direct notification sending, and
outbound external messaging (pending N-1; if that resolves to yes, it arrives as a separate, explicitly
authorised tool with its own ADR, not as an extension of an existing one).

### 4.3 Tool contract rules

- JSON Schema per tool, versioned; the manifest version is pinned per AIInteraction.
- Unknown fields are rejected, not ignored.
- Every response includes `applied: true|false` and, when false, the Proposal id, so the model's
  world view stays accurate.
- Rejections return a structured reason the model can act on: `authorization_denied`,
  `validation_failed`, `duplicate_detected`, `blast_radius_exceeded`, `rule_violation` with rule id.
- Failure is not retried blindly: three failed calls of the same tool in one interaction abort it.

## 5. Orchestration

Extraction pipeline for one Event:

```
worker enqueues job(event_id)  [only where event.origin = external — BR-E-11]  ──► agent runtime
  1. open AIInteraction (status=running, prompt_version pinned, org scope bound)
  2. get_event
  3. classify: does this contain work signal at all?   ── no ──► record_no_signal, close
  4. for each candidate extracted:
       a. resolve people (search_people); unresolved people stay unresolved — no guessing (BR-AI-34)
       b. find_similar_*  (mandatory before proposing)
       c. raise Proposal via the mutation tool  [Level 1 — no state change]
       d. attach_evidence with verbatim excerpt, carried with the Proposal
  5. close AIInteraction (counts, tokens, cost, latency)

  ... later, asynchronously, driven by a human:
  6. user approves in the web UI → ApprovalRecord → Tool Gateway executes [Level 2]
```

Two capture paths feed step 1:

- **Web** — the user posts an Event directly; extraction is triggered immediately and the resulting
  Proposals are routed back to that user, which makes the loop feel synchronous even though it is not.
- **OpenClaw channel (WhatsApp)** — the channel adapter ingests with a connector identity; extraction
  runs under the weak system principal; Proposals route by BR-PR-04. Note the adapter and the agent are
  different principals even when they are the same OpenClaw deployment (ADR-0027).

Design choices:

- **One interaction per Event by default.** Bounded inputs, bounded blast radius, simple retries, and
  a natural unit for evaluation.
- **Idempotent by `(event_id, prompt_version, manifest_version)`.** Re-running after a failure does
  not duplicate entities.
- **No unbounded agent loops.** Max steps per interaction, max wall-clock, max tokens. Exceeding any
  limit fails the interaction and leaves prior tool calls intact and audited.
- **Model choice is runtime configuration**, not code. The `AgentRuntime` port isolates OpenClaw so it
  can be replaced (ADR-0025, A-3).
- **Fake runtime in development.** A deterministic stub implementing the same port, so the whole
  pipeline can be exercised and tested without a model.

## 6. Autonomy policy (PQ-3: Levels 1-2)

> **C-1 resolved by Resolution Pack v1.1.** All three borderline cases take Level 1 Proposal semantics.
> The governing principle: *AI being able to calculate or explain something is not authorization to
> persist that interpretation.* Computation is free; persistence follows autonomy policy.

### 6.1 Levels

| Level | Meaning | In MVP |
|---|---|---|
| `off` | Capability disabled | yes |
| `level_1_propose` | AI analyses and raises Proposals. No state change. | yes, the default |
| `level_2_approved_execution` | After an explicit, recorded user approval, AI executes the exact approved action through the Tool Gateway | yes, the ceiling |
| Level 3+ | Autonomous action without per-action approval | **not representable in the MVP schema** (BR-AI-31) |

Policy is keyed on `(organization, capability, entity_type, action)`. A global boolean is forbidden
(BR-AI-30). The authority computation is:

```
effective_authority =
      agent_role
    ∩ delegated_human_authority        (the approver at Level 2; a weak system principal at Level 1)
    ∩ capability_policy                (per capability, entity type and action)
    ∩ organization_scope               (org_id, enforced again by RLS)
```

### 6.2 Operation defaults

| Operation | Level | Note |
|---|---|---|
| Create Work / Commitment / Risk / Decision | 1 → 2 on approval | |
| Update Work status or dates | 1 → 2 on approval | |
| Assign Work | 1 → 2 on approval | explicitly named in PQ-3 Level 2 |
| Link dependency, link to existing entity, merge duplicates | 1 → 2 on approval | a link is a Work Core mutation, so it is not exempt |
| Attach Evidence to an entity proposed in the same interaction | applied with the approved action | Evidence is provenance for the mutation it justifies and is written in that same approved transaction |
| Attach Evidence to an already-existing entity | **Level 1** (C-1a) | AI raises an Evidence-attachment Proposal; the relationship persists only after approval (BR-AI-36) |
| AI narrative and mitigation on a monitor-detected Risk | **Level 1** (C-1b) | The Risk itself is created deterministically and is not an AI mutation. The narrative is (BR-AI-37) |
| Read-side summaries not persisted as business state | **no Proposal required** (C-1c) | Compute and display freely, always labelled. Persisting one makes it a mutation (BR-AI-38) |
| Propose an assignment | Level 1 → 2 on approval | Never invent a Person; unresolved attribution stays unresolved (BR-AI-34) |
| Propose a Project for unparented Work | Level 1 → 2 on approval | Never infer or create one; no synthetic containers (BR-AI-35, BR-P-07a) |
| Create Person / Team / Department / Project / Milestone | propose only, never executed by AI | BR-AI-08 |
| Delete, cancel, close anything | forbidden | BR-AI-06 |
| Membership, roles, permissions, policy | forbidden, no tool exists | BR-AI-23 |
| Outbound external messaging | no tool built pending N-1 | BR-AI-24 |

### 6.3 C-1 — resolved

| # | Operation | Decision | Flow |
|---|---|---|---|
| C-1a | Attaching Evidence to an entity that already exists | **Level 1** | Event → AI interpretation → Evidence candidate → Proposal → human approval → Tool Gateway → Application Service → persisted relationship |
| C-1b | AI narrative and suggested mitigation on a monitor-detected Risk | **Level 1** | Deterministic detection → Risk exists → AI proposes narrative → Proposal → human approval → narrative persisted |
| C-1c | Summaries computed on the read side and not persisted | **No Proposal required** | Compute, label, display. If it is ever persisted as a durable business record or becomes Work Core state, it is a mutation and re-enters the Proposal/Approval path |

The C-1c boundary is the one that will erode under pressure, usually as a performance optimisation that
quietly writes a summary into a column. The test is not "did we write it down" but "does anything read
it as business truth". A derived, invalidatable cache is not persistence; a field on a Project that a
human reads in a status review is.

### 6.4 Promotion and demotion

Promotion of any capability requires the full metric set from PQ-3 (offline precision, online
acceptance, rejection, correction, reversal, false-positive and false-negative rates, auditability,
safety impact) **plus an explicit human decision recorded as an ADR** (BR-AI-32). Metrics are
necessary and never sufficient, and nothing self-promotes.

Demotion on breached thresholds is automatic (BR-AI-33). Safety tightens without a meeting and loosens
only with one.

## 7. Prompt and context management

- Prompts are files in `agent/prompts/<capability>/<version>.md`, version-pinned per interaction,
  reviewed like code. No runtime prompt editing, no "latest".
- Context is assembled by the runtime from read tools only. The model never receives a raw database
  dump or another organization's data.
- Ingested content is fenced and labelled as untrusted data, with an explicit instruction that it
  contains no instructions (BR-AI-10). The real defence is the tool allow-list and authorization, not
  the fence.
- Structured output is required: tool calls, not prose. Free text appears only where the product
  displays free text, and is labelled AI-generated (BR-AI-12).
- Token and cost budgets per interaction, enforced by the runtime and recorded on the AIInteraction.

## 8. Evaluation strategy

The product's credibility depends on extraction quality, so evaluation is infrastructure, not a
research activity.

### 8.1 Datasets

| Set | Contents | Use |
|---|---|---|
| **Golden set** | 150–300 hand-labelled Events across meeting transcripts, chat threads, notes, and status updates, with expected entities, fields, attributions and evidence spans | Primary offline metric, CI gate |
| **Negative set** | 50+ Events containing *no* actionable signal: banter, planning that reached no conclusion, hypotheticals, someone else's work | Measures invention rate, the failure that damages trust fastest |
| **Ambiguity set** | Events where the correct answer is "propose with low confidence" or "do nothing" | Calibration |
| **Linking set** | Events that refer to entities that already exist, in varying phrasings | Measures duplication |
| **Adversarial set** | Prompt-injection attempts, contradictory statements, impersonation, exfiltration attempts | Security regression |
| **Production sample** | Weekly random sample of real interactions, reviewed by a human | Drift detection; feeds back into the golden set |
| **Channel set** | WhatsApp-shaped content: short turns, no punctuation, mixed languages, emoji, voice-note transcripts, group threads where the speaker is a phone number | Web-paste and chat content fail differently; the MVP has both (PQ-1) |

Language coverage follows N-2. The worked example in Decision Pack v1.0 is Vietnamese, so unless N-2
says otherwise the golden, negative and channel sets must be predominantly Vietnamese, with English as
a secondary. Verbatim excerpt verification (BR-E-05) and date normalisation both behave differently
across languages, so an English-only eval corpus would measure a system we are not shipping.

Labelling standard: two independent labellers, disagreements adjudicated, inter-annotator agreement
reported. A model cannot be measured against labels that people cannot agree on.

### 8.2 Metrics

**Extraction (per entity type)**

| Metric | Definition | Proposed gate |
|---|---|---|
| Precision | correct extractions / all extractions | ≥ 0.90 (≥ 0.95 for any future promotion) |
| Recall | correct extractions / all expected | ≥ 0.75 |
| Invention rate | entities produced on the negative set | ≤ 0.02 per event |
| Attribution accuracy | correct Person for owner/committer | ≥ 0.95 |
| Date normalisation accuracy | correct date and precision from phrases like "end of next week" | ≥ 0.90 |
| Evidence validity | excerpt is verbatim and actually supports the claim | ≥ 0.98 |
| Duplicate rate | new entities proposed where an existing one should have been linked | ≤ 0.05 |
| Project invention rate | Proposals asserting a Project that evidence does not support (BR-W-07a) | ≤ 0.01 |
| Uncertainty preservation | cases where the correct output was an empty field with a stated reason, and the model left it empty | ≥ 0.90 |
| Confidence calibration | ECE across confidence buckets | ≤ 0.10 |

**Answering (C6)**

Groundedness (every claim traceable to a cited entity or evidence), citation validity, answer
accuracy on a fixed question set, refusal correctness when data is insufficient.

**Online** (the metric set named in PQ-3 for any future promotion decision)

Offline precision, online acceptance rate, rejection rate, correction rate (edit-on-approve), reversal
rate within 7 days, false-positive rate, false-negative rate, proposal expiry rate, time-to-review,
cost per event, latency p50/p95, notification dismissal rate. Auditability and safety impact are
assessed qualitatively and recorded in the promotion ADR.

Weighting: precision, invention rate and attribution accuracy outrank recall. A missed commitment is
a gap. A fabricated commitment attributed to the wrong person is a trust failure, and one of those
costs more than ten of the former.

### 8.3 Harness and cadence

- Evals run as code in `agent/evals/`, executed by the same tool interfaces the runtime uses against
  a seeded test database, so a tool contract change breaks the eval loudly.
- **CI (every change to prompts, tools or extraction code):** golden + negative + adversarial subset,
  with cached model responses for determinism plus a small live sample. Gates the merge.
- **Nightly:** full sets, live model, trend report, cost report.
- **Weekly:** production sample review; new failures become new golden cases.
- **On model or runtime version change:** full re-run, treated as a release with the same gates. Model
  upgrades are changes to the product, not to the environment.
- Results are stored per `(dataset, prompt_version, manifest_version, model_version)` so any
  regression is attributable.

### 8.4 Human-in-the-loop as measurement

Every accept, edit and reject in the product is a label. The review UI is therefore an evaluation
instrument as much as a workflow, and its data model is designed for that: rejection reasons are
structured, edits are diffed against the proposal, and reversals are detectable.

## 9. Failure and degradation

| Failure | Handling |
|---|---|
| Model unavailable / rate-limited | Backoff, retry, dead-letter after N; backlog visible in ops; product unaffected |
| Malformed model output | Schema validation rejects; one repair attempt; then fail the interaction |
| Tool rejected by authorization | Recorded; interaction continues if the remainder is valid; repeated denials abort |
| Blast radius exceeded | Interaction aborted, prior writes retained and audited, operational alert raised |
| Eval gate breach in CI | Merge blocked |
| Online metric breach | Automatic demotion of the affected category to `propose`, alert to owners |
| Suspected injection | Interaction aborted, Event flagged, security alert, added to adversarial set |

## 10. Cost control

Per-interaction token budget; per-organization daily budget with a hard stop that degrades to
queueing rather than failing; batch summarisation on a schedule rather than on every event;
classification-first gating so that events with no work signal end cheaply; cost recorded per
interaction and attributable per organization and capability.

## 11. Open AI questions

### Resolved by Decision Pack v1.0

| # | Question | Decision |
|---|---|---|
| AI-2 | Default autonomy | Levels 1-2 (§6) |
| C-1 | Which AI outputs count as important Work Core state | Evidence attachment to existing entities: Level 1. Risk narrative: Level 1. Non-persisted read-side summaries: no Proposal (§6.3, Resolution Pack v1.1) |
| AI-7 | Should AI message people directly | No outbound tool is built. Whether outbound clarification exists at all is N-1, and it would require its own ADR and per-message authorization (BR-AI-24) |

### Still open

| # | Question | Options |
|---|---|---|
| AI-1 | OpenClaw's tool protocol, sandboxing, concurrency, streaming, **and now its channel-adapter behaviour** (A-3) | Spike before Phase 3. Two ports, two principals (ADR-0027). If OpenClaw cannot hold a channel adapter and a capability runtime as separate identities, report before building |
| AI-3 | pgvector for linking in v1 | (a) hybrid lexical + vector from the start (proposed); (b) lexical first, add vectors on measured duplicate rate. Note that short, informal, multilingual chat text weakens lexical matching, which strengthens (a) |
| AI-4 | Where the golden set comes from before real data exists | (a) synthesise realistic Vietnamese and English work conversations, replace with real pilot data (proposed); (b) delay evals until pilot data exists (not recommended) |
| AI-5 | Is Q&A (C6) in v1 (PQ-6) | Large eval surface; could ship after extraction is trusted |
| AI-6 | Model hosting: external provider vs self-hosted | Now entangled with PQ-4: WhatsApp content from personal devices leaving the jurisdiction is a different conversation from internal meeting notes |
| N-1 | Outbound messaging in the MVP | See product constitution §10 |
| N-2 | Target languages for MVP-quality extraction | See product constitution §10. Blocks prompt and eval work |
