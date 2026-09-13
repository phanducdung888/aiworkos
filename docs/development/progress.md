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
| 1 | Work Core (no AI) | ✅ complete — checkpoints 1–5.1 | A team can manage real work in it with AI off |
| 2 | Signal capture & evidence | ✅ complete — checkpoints 6–7 | Events ingested, evidence attachable by hand, corpus collecting |
| 3 | Tool Gateway & extraction | 🟡 in progress — checkpoints 8–18; pilot use not yet attempted | Proposals from real activity, accepted by real users |
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

**OpenClaw: SPIKE FURTHER (ADR-0053).** Not adopted and not rejected — the repository holds no
verified API documentation, so there is nothing to adopt against and nothing establishing it is
unsuitable. Six questions must be answered by a spike first; the one that decides it is whether
OpenClaw can present two distinct identities, because ADR-0027 requires the channel adapter and the
capability runtime to authenticate separately. Worth separating for whoever runs it: the **channel
adapter** role has clear value and a narrow blast radius; the **capability runtime** role is the
contested one, and the two can be decided independently.

**The intent kind vocabulary is three entries.** `CREATE_WORK`, `CREATE_COMMITMENT`, `ASSIGN_WORK`.
An agent cannot express anything else, which is correct today and is a ceiling: adding a capability
means teaching `IntentValidator` about it, and that is deliberate friction rather than a gap.

**Commitment degradation is a heuristic about shape, not about content.** A promise with no resolved
speaker becomes Work. That preserves the observation and drops the unevidenced attribution, and it
is a judgement — a reviewer sees a Work item where a human might have recorded a commitment against
a name they could infer from context the system cannot see. Visible in the Proposal's reason, and
worth revisiting once real extraction volume exists.


**A failed provider run leaves no AIInteraction row.** The runtime marks the interaction `failed`
before re-raising, and `scoped_session` then rolls the request back — deliberately, because a
service that kept an audit entry for a mutation that failed would be lying. So a provider outage is
visible as a 502/504 and in logs, not as a row. Recording it durably needs a second transaction, and
that belongs with whatever writes operational telemetry rather than bolted into the orchestration.
Pinned by `test_a_malformed_provider_answer_is_not_a_low_confidence`, which asserts the count does
not move.

**Band floors are still uncalibrated, and now say so.** `HIGH = 85`, `MEDIUM = 60` are the
Checkpoint 9 numbers carried forward unchanged, so that this checkpoint changed the *semantics*
without also changing behaviour. They are now labelled with their source, which is the part that
makes them tunable later; tuning them needs outcome data that does not exist. A provider reporting
log-probs or nothing at all still needs a documented mapping before it goes on a real path.

**`AnthropicProvider` has never spoken to Anthropic in CI.** Its unit tests drive a stubbed
transport and cover the mapping — statuses to typed errors, malformed bodies to contract failures,
the credential never reaching a return value. What they cannot cover is whether the real API's
response shape matches what the adapter parses. The opt-in test exists for exactly that and has to
be run deliberately, with a key, before the adapter is trusted.

**No batch or scheduled analysis path.** Analysis is triggered per Event over HTTP. A provider
outage therefore fails one request rather than stalling a queue, which is convenient and is not a
design — when analysis becomes scheduled, the retry and rate-limit semantics of `ProviderError`
will need somewhere to be honoured.


**`find_similar` matches titles only.** Trigram over `work.title`, so two items describing the same
thing in different words do not match — "Send the revised quote" and "Get the updated pricing to
Nam" are the same task and score near zero. BR-AI-05 is satisfied (the agent looks, and the search
is recorded) and the *quality* of the looking is limited. Embeddings would fix it and a vector store
is not being introduced on speculation; the interface returns references, so replacing the
implementation changes nothing above it.

**The agent registry is still code.** `AGENTS` in `app/api/v1/agent.py` names one agent and its
capabilities. The *policy* is now per-organization data, which was the urgent half; which agents
exist and what each is built to do is still a deployment. That is the right order — a policy row
cannot widen an agent that was never built to do the thing — but a second agent will want a table.

**No execution window.** BR-AI-22 says an ApprovalRecord not executed within 24 hours expires and
must be re-approved. Nothing expires one today: a job that fails repeatedly reaches `dead` and its
approval stays `pending` forever. The single execution path (ADR-0048) is what makes this
implementable in one place, and it is not implemented.

**Confidence bands are uncalibrated.** `Confidence.HIGH = 85` and `MIN_CONFIDENCE = 60` are numbers
chosen so the fake provider's output crosses the threshold. They mean nothing yet, and BR-AI-09's
"below threshold produces no Proposal" is only as good as the mapping a real provider gets. Deciding
it silently when the adapter is written would make the rule decorative.


**The `job` table is readable without an organization context.** One deliberate exception to
default-deny (ADR-0044): a worker serves every tenant, cannot know which has work before it looks,
and no role holds BYPASSRLS. Reading and claiming are exempt; **enqueueing is not**, so nothing can
manufacture work for a tenant it was not asked to act for. Pinned by
`test_the_job_queue_is_the_only_unscoped_readable_table`, which fails if a second table ever gets
the same policy. The residual risk is real and bounded: a session that forgets to scope can see
queue rows — job kinds and approval ids, no content.

**Agent capabilities are code, not configuration.** `AGENTS` and `DEFAULT_POLICY` are constants in
the API module. BR-AI-30 requires policy keyed per `(organization, capability, entity_type,
action)`, and that table does not exist yet — so today every organization gets the same policy and
changing it is a deployment. That is the safe direction to be wrong in, and it must be resolved
before a second agent or a second capability ships.

**Execution can still run in-request.** `POST /approvals/{id}/execute` remains alongside the queued
path, because the CP7 tests exercise it and removing it was not in scope. Both go through the same
`ProposalService.execute`, so the safety properties are identical — but two paths to a mutation is
one more than the design wants, and the synchronous one should go once the worker is deployed.

**No `find_similar` tool, so BR-AI-05 is unenforced.** The rule requires a similarity search before
proposing a new entity, to stop the agent proposing duplicates of things that already exist. The
tool does not exist and the runtime does not call one. Nothing currently proposes at a volume where
it matters; that stops being true with a real provider.


**Creating Work with an unresolvable `project_id` returns 404.** `WorkService.create` loads the
Project — it needs the row for visibility computation — and raises `EntityNotFound`, so a body field
that does not resolve is reported as though the addressed resource were missing. This is the same
inconsistency found and fixed for assignments in Checkpoint 5.1, still present on this path. Left
alone deliberately: it is Work Core behaviour that predates this checkpoint and nothing in CP7
required touching it. Pinned by a comment in `test_execution_atomicity.py`, which asserts the
rollback property rather than the status code.

**Evidence has no `ai_interaction_id` and Proposal has no link to one.** `produced_by_type` already
distinguishes `person` from `ai_interaction`, and `produced_by_id` is where the interaction id will
go, but AIInteraction does not exist — so BR-AI-02's "carries an `ai_interaction_id`" is currently
satisfied by attribution to a person instead. The extraction checkpoint adds the entity and the
column; nothing needs to change in the chain's shape.

**`raised_by_ai` is a caller-supplied flag, not a derived fact.** BR-AI-02 and BR-C-03 both key off
it, and today an API caller could raise a Proposal claiming human origin and skip the
evidence requirement. That is not currently exploitable — every path through the API *is* a human —
but it stops being safe the moment an agent has an API credential. The fix belongs with the agent
runtime, which is where the distinction becomes real.

**Level 2 execution is synchronous and in-request.** Approving and executing are separate endpoints,
which is what makes the binding checkable, but execution still runs inside the approver's request.
A queued executor is the natural next step and changes nothing about the model — the ApprovalRecord
already carries everything needed to run it later, which is why approval and execution were split.


**Evidence has a table and no way to create one.** Migration 0009 creates `evidence` with its
invariants — BR-E-04 structurally (a composite foreign key to the Event in the same organization),
BR-E-05 and BR-E-14 as a check constraint (verbatim `excerpt` or a `claim_summary`, exactly one),
BR-E-06 as an immutability trigger leaving only `superseded_by_id` writable. There is no service, no
endpoint and no `EVIDENCE` authorization resource, because who may produce Evidence is a question
extraction answers and extraction is not built. The table exists now so Events captured today are
citable later without a backfill inventing provenance for rows already written.

**N-4 is still open and now has code standing on it.** BR-E-16 says nothing may read `COMMENT` Events
for extraction until N-4 is decided. The capture API enforces the stricter reading: `COMMENT` is
internal-origin by BR-E-15 and `POST /api/v1/events` writes external Events only, so a comment cannot
be captured through it at all. Whether in-app conversation is a capture surface is what N-4 asks, and
answering it changes this endpoint.

**Sensitivity is enforced for reading, not for setting.** BR-E-08 narrows who may *read* a
`restricted` Event and `queries._sensitivity_predicate` implements exactly that. Nothing stops a
member marking their own capture `restricted` and hiding it from everyone but its participants and
the auditors. That may be correct — it is the same discretion a person has over something they never
wrote down — but no rule states it either way.

**An archived Team or Department still accepts new work.** `assert_team_exists` checks tenancy, not
status, so a team archived this morning can own a Project created this afternoon. BR-I-05 settles
the analogous question for people — a departed Person takes no new work — and no rule states the
unit equivalent, so this is undecided rather than decided. Pinned by
`test_an_archived_team_can_still_be_given_new_work_today` so that changing it is visible.

### Resolved

**The attachment flow had never been executed (closed in CP22).** `S3ObjectStore` is the only
module importing boto3 and the one that issues credential-bearing URLs, and it had no test of
any kind — every attachment test installs the in-memory store. MinIO also published no ports,
so a presigned URL was unreachable from the host by any client. Both fixed; the whole ADR-0039
sequence now runs against a real store, including expiry, key tampering and unsigned access.

**CP21's compose file broke every `docker compose` command (closed in CP22).** `${VAR:?...}` on
the `pilot`-profile connector service failed interpolation for the whole file — including
`up postgres` — because compose interpolates before it applies profiles. The variables are
passed through plainly now; the connector already refuses to start naming what is missing.

**The connector's IMAP conversation was untested (closed in CP21, ADR-0062).** CP20 covered the
normaliser and the delivery client and left the `imaplib` loop with no test at all. Driving it
against a real mail server found a real defect on the first run: `Internaldate2tuple` returns a
local-time struct, and the fallback timestamp was being relabelled UTC rather than converted —
seven hours out on the machine it was found on, on exactly the field CP15 reads deadlines
against. Pinned now by a test that needs no server.

**PQ-1 had no buildable external connector (closed in CP20, ADR-0061).** Its only sanctioned
external-message source was OpenClaw/WhatsApp, which ADR-0059 showed publishes no outbound
delivery mechanism; its named non-goals covered every alternative with a verified protocol.
Reported as a conflict rather than resolved in code, and the Product Owner amended PQ-1 to
permit **email over IMAP** as the first external connector. OpenClaw/WhatsApp stays sanctioned
and unbuilt until a verified external interface exists.

**ADR-0027's ingestion-only identity did not exist (closed in CP20, ADR-0060).** A connector
would have run as `member` — 56 of the matrix's 83 cells, including `PROPOSAL.APPROVE`, so a
stolen delivery credential could have approved the AI's own proposals. `Role.INGESTION` holds
one grant: `EVENT.CREATE`. It cannot approve, write business state, run the agent, change the
agent policy, confirm an identity, or read anything back — including the Event it delivered.
Migration 0014 widens one CHECK constraint and changes nothing else.

**A-3 / OpenClaw's channel-adapter question (closed in CP19B, ADR-0059).** The spike ADR-0053
called for was done against the published documentation rather than against expectation.
OpenClaw documents WhatsApp as an inbound channel *inside its own gateway runtime* — a CLI,
JSON5 routing config, Baileys credentials on disk — and publishes **no outbound delivery
mechanism**: no webhook, no callback, no event schema for external consumption. There is
nothing for an adapter to be written against, and the credential separation ADR-0027 asked
about is internal to one gateway rather than the external boundary it required. The only shape
specifiable today reverses the direction: something beside the gateway calls WorkOS's capture
API under ADR-0058. The capability-runtime half of A-3 stays open.

**Commitments had no deadlines (closed in CP15, ADR-0055).** `due_date` and `due_precision` were
on the agent's allow-list, accepted by the Tool Gateway and present on the table, and nothing
populated any of it — the extraction schema never asked. Asking the model for a date was the
wrong fix: it has no clock and would answer anyway. The model now quotes the deadline phrase and
WorkOS reads it against the Event's `occurred_at` and the organization's timezone, declining
far more often than it answers. The agent may no longer supply a date or a precision at all.

**Provenance was one-directional (closed in CP15, ADR-0056).** BR-PR-08 wants the chain walkable
both ways and there was no route from a Commitment back to its Proposal. Fixed with one optional
filter — `GET /proposals?resulting_entity_id=` — through `ApprovalRecord`, the only row in the
chain execution is allowed to write. No column was added to hold a copy of something derivable.

**PQ-7 — external identity resolution (closed in CP14, ADR-0054).** Resolution is lookup-only
by `(org_id, source_system, external_id)`; it creates no `ExternalIdentity`, no Person, and
infers nothing from names. Attribution requires `confirmed_at IS NOT NULL AND confidence >= 90`,
asked through `identity.may_attribute` — which until CP14 was a rule nothing on any path called.
Below the threshold the handle is preserved, `person_id` stays NULL, and the existing
conservative refusals apply unchanged. Attribution by name similarity and by unconfirmed
mapping were both rejected; M-8/M-9 (channel thread id, phone identity typing) stay open.

**BR-AI-05 searched the wrong corpus (closed in CP14).** Every accepted intent was checked for
duplicates against *Work titles*, commitments included — so a promise could be suppressed by an
unrelated task worded alike, and a promise the same person had already made was proposed again.
Commitment intents now search standing commitments by that committer, and the `tool_call` row
records which corpus was searched. See the CP14 limitation on the unindexed statement scan.

**A control existed and was never called (closed in CP11).** `assert_within_agent_authority` held
BR-AI-08 and BR-AI-23, was unit-tested, and nothing invoked it for two checkpoints — ADR-0047
described it as "applied last" and it was applied nowhere. It is now on the intent path, and
`test_every_declared_control_is_actually_invoked` asks the question that was never asked: not "is
this correct" but "is this called".

**Model output could name a Person (closed in CP11).** `span.attributes` could supply
`committed_by_person_id`, and when it did not, the runtime defaulted to the *delegating human* — so
an extracted promise was attributed to whoever ran the analysis. An agent is now never given a
person id at all: it points at participants the Event resolved, or the intent is refused (BR-AI-34,
ADR-0052).

**The agent constructed its own Proposals (closed in CP11).** Every control on that path lived
inside the component the controls existed to constrain. An agent now emits `ToolIntent`s and
WorkOS decides what becomes a Proposal — which is also the boundary an external agent would enter
through.

**Confidence was a bare number with no provenance (closed in CP10).** `HIGH = 85` and
`MIN_CONFIDENCE = 60` were integers chosen so the fake provider crossed the threshold. A threshold
tuned against a mixture of calibrated probabilities, adapter heuristics and defaults means nothing.
Confidence now carries its source and `UNKNOWN` is first-class — a missing confidence never becomes
HIGH, a default or a midpoint (ADR-0050).

**ApprovalRecords could stay pending forever (closed in CP10).** BR-AI-22's window is enforced
inside the claim, and the deadline is derived from the immutable `decided_at` rather than stored —
so it cannot drift between a worker's retries (ADR-0051). Expiry is terminal rather than retryable.

**The provider interface could not express failure (closed in CP10).** Six typed errors, and a
malformed answer is a contract violation rather than a low confidence — so an outage no longer looks
like a quiet day (ADR-0049).

**The job queue was readable without an organization context (closed in CP9).** The Checkpoint 8
exemption was keyed on the *absence* of a setting, so any session that forgot to scope — including
one serving an HTTP request — could read the queue. A condition granting access when a variable is
unset is the opposite of default-deny. The exemption is now an identity: `workos_worker`, one policy
on one table (ADR-0046). Two tests hold the shape — no policy anywhere may test for a missing
organization, and `workos_worker` appears in exactly one policy.

**Agent capability policy was hard-coded (closed in CP9).** Two module-level constants meant every
organization shared one policy and changing it was a deployment, which BR-AI-30 explicitly forbids.
Now a tenant-scoped table where absence is denial (ADR-0047).

**Two paths to an approved mutation (closed in CP9).** The synchronous endpoint is gone (ADR-0048).
Every future execution control — a rate limit, a window, a kill switch — is now implementable once
rather than twice-or-bypassable.

**BR-AI-05 was unenforced (closed in CP9).** `find_similar_work` exists, the runtime calls it before
proposing, and the search is recorded as its own tool call — so "did this interaction look first" is
answerable from the audit trail rather than by trusting the code did.

**`raised_by_ai` was caller-supplied (2026-09-12, the CP7 risk, closed in CP8).** Two rules keyed
off a boolean the request could set. Nothing exploited it because every path through the API was a
human and the schema never exposed the field, but it was a security property held up by the fact
that nobody had wired the other case. Origin is now read from the `Actor` — specifically from
`ai_interaction_id`, not from `type`, so an approved AI Proposal executing under the approver's
signature is still recognised as AI-sourced (ADR-0043). `test_no_request_field_can_claim_ai_origin`
walks the request schemas to keep the field from coming back.

**A permanently failing job retried forever (2026-09-12, found in Checkpoint 8).** The worker rolls
the transaction back before recording a failure, which also rolled back the claim's
`attempts = attempts + 1`. The counter never moved, `dead` was unreachable, and the
`job_dead_is_exhausted` constraint fired when the in-memory count disagreed with the row. `fail()`
now writes `attempts` as an absolute value taken from the claim, which survives in memory precisely
because it is not in the transaction that was rolled back.

**An AI-proposed Commitment did not carry its Evidence into the action (2026-09-12, found in
Checkpoint 8).** The Proposal cited Evidence, but the `create_commitment` arguments did not, so the
Commitment built from them failed BR-C-03 at execution. Caught by the rule doing its job at exactly
the right moment — after approval, before the write.

**Nullable JSONB columns compared as JSON `null`, not SQL NULL (2026-09-12, found in Checkpoint
7).** SQLAlchemy renders a Python `None` into a JSONB column as the JSON value `null` by default, so
a check constraint written as `edits IS NULL` was false for a row that looked empty in every query.
`approval_edits_accompany_an_edited_decision` fired on ordinary approvals. Fixed with
`JSONB(none_as_null=True)` on every nullable JSONB column in the Intelligence models. The class of
bug is worth remembering: the schema and the ORM disagreed about what emptiness meant, and nothing
surfaced it until a constraint did.

**Evidence silently dropped a `claim_summary` supplied with a text quote (2026-09-12, found in
Checkpoint 7).** The service narrowed to the locator's kind and discarded the other half, so a
caller sending both stored something other than what they sent. Now refused by the request schema
with the rule named. Storing a quietly different thing from what was asked for is the same class of
defect as an executor dropping an argument, which is what ADR-0041 exists to prevent.

**Attachment authorization could be bypassed for a restricted Event (2026-09-12, found in
Checkpoint 6).** `AttachmentService` loaded the Event through `repository.get_event`, which filters
by organization and nothing else. BR-E-08's narrowing lives in the query layer, so a `restricted`
Event loaded that way passed the subsequent `READ` authorization — the matrix grant is
organization-wide and the narrowing is not in the matrix. Anyone in the organization holding an
attachment id could obtain a presigned download URL for a conversation they could not read. Fixed by
loading through `queries.get_event`. This is the exact shape of the bug ADR-0039 claims the design
makes unrepresentable, and it was unrepresentable only once the load path went through the
read-filtered query.

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
| 6 | Signal/Capture: Event, EventParticipant, EventAttachment, Evidence foundation; capture API; ObjectStore port; ADR-0038/0039 | ✅ complete · 552 backend + 38 frontend |
| 7 | Evidence, Commitment, Proposal + ApprovalRecord, Tool Gateway; ADR-0040/0041/0042 | ✅ complete · 748 backend + 38 frontend |
| 8 | AgentRuntime, AIInteraction, agent authority, queued execution; ADR-0043/0044/0045 | ✅ complete · 808 backend + 38 frontend |
| 9 | Capability policy, job isolation, single execution path, find_similar; ADR-0046/0047/0048 | ✅ complete · 843 backend + 38 frontend |
| 10 | Provider contract, confidence semantics, execution window; ADR-0049/0050/0051 | ✅ complete · 930 backend + 38 frontend |
| 11 | Agent Contract / ToolIntent, IntentValidator, G1+G2 fixes; ADR-0052/0053 | ✅ complete · 957 backend + 38 frontend |
| 12 | Real-provider smoke tests, opt-in and skipped by default; `AgentContract` implemented | ✅ complete · 992 backend + 38 frontend |
| 13 | OpenAI provider, vendor-neutral span parsing, span-offset realignment fix | ✅ complete · 1020 backend + 38 frontend |
| 14 | External identity resolution (PQ-7), commitment duplicate routing, capture/proposal/approval UI; ADR-0054 | ✅ complete · 1049 backend + 61 frontend |
| 15 | Deadline reading (quote, never compute), canonical provenance walk, commitment list/detail and lifecycle UI; ADR-0055/0056 | ✅ complete · 1103 backend + 74 frontend |
| 16 | Work brought to the same standard: provenance panel on Work, created-entity links, the four blanks the AI may not fill asserted | ✅ complete · 1109 backend + 81 frontend |
| 17 | Attention view: six deterministic sections over existing endpoints, each stating the rule that produced it; no new API, no AI scores | ✅ complete · 1115 backend + 92 frontend |
| 18 | Product integration: lands on Attention, decided proposals readable again, proposal history, "the agent is switched off" distinguished from "the agent found nothing", shell coherence tests | ✅ complete · 1115 backend + 101 frontend + 5 journeys |
| 19A | Agent policy surface: derived editable grid, a cell's action made load-bearing, admin UI; ADR-0057 | ✅ complete · 1127 backend + 111 frontend |
| 19B | Ingestion contract: no connector port, the capture API is the seam; executable conformance suite; OpenClaw spike closed on evidence; ADR-0058/0059 | ✅ complete · 1139 backend + 111 frontend |
| 20 | First real connector: IMAP email, ingestion-only role, migration 0014, PQ-1 amended; ADR-0060/0061 | ✅ complete · 1169 backend + 111 frontend + 45 connector |
| 21 | Connector operations: real mail-server tests (found a timezone defect), TLS modes, service loop, connector topology confined and asserted; ADR-0062 | ✅ complete · 1169 backend + 111 frontend + 81 connector |
| 22 | Attachment substrate: `S3ObjectStore` and the whole ADR-0039 flow verified against real MinIO for the first time; compose interpolation defect fixed; connector attachment delivery blocked and specified (ADR-0063) | ✅ complete · 1183 backend + 111 frontend + 81 connector |

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
## Measurements

Recorded so the next person does not have to re-derive them, and so an index is added because
something is slow rather than because an index seemed prudent.

### Commitment duplicate search (CP15, unindexed `commitment.statement`)

Trigram similarity filtered by `(org_id, committed_by_person_id, status)`, median of five runs on
the development database:

| standing commitments *for one person* | median |
|---|---|
| 1,000 | 16 ms |
| 10,000 | 143 ms |
| 50,000 | 727 ms |

Linear, as a sequential scan computing `similarity()` per row must be. The filter is per *committer*
— a person makes a few hundred promises a year, so 1,000 is already an implausible pilot-scale
ceiling and 16 ms is noise beside the provider call that precedes it in the same request.

**Index deferred.** `ix_commitment_statement_trgm` becomes worth a migration at roughly 5,000
standing commitments per committer (~70 ms), or if the committer filter is ever relaxed. Neither is
true, so CP15 added no migration for it.

### Test isolation and the shared job queue (CP16 flake, investigated in CP19B)

A single full-suite run during CP16 reported one failure whose name was not captured. It did not
reproduce in **eleven** subsequent full or integration-wide runs, nine of them in shuffled file
order.

The mechanism that *could* produce it is understood and is worth writing down. `job` is the one
table a worker reads across organizations by design (`claim()` has no `org_id` predicate, because a
worker serves every tenant and scopes itself from the row it claims). `execute_approval` in the test
conftest therefore drains jobs other tests left behind. Every assertion those tests make is
org-scoped, so a foreign job cannot corrupt a count — but a job that failed and rescheduled itself
would have spun the drain loop forever.

**Fixed at that level and no further**: the drain is now bounded, so a retry loop fails by name in
seconds instead of hanging.

**It recurred once in CP22** — one shuffled run in seven, fourteen execution tests failing together
with "expected executed, got pending". Still not reproducible: six clean runs before and after, and
targeted orderings of the implicated files pass. The queue table held nothing pathological
afterwards.

**CP22 reproduced it and instrumented it, and it is still open.** Across roughly seventeen shuffled
full runs, three failed — always the same signature: an approval left `pending` because its job was
not executed by the drain. Targeted orderings of the implicated files pass every time, and the queue
table holds nothing pathological afterwards.

`execute_approval` now does two things it did not. It asserts on its own queue call, because a queue
request that failed left no job and the test then failed several assertions later describing the
symptom rather than the cause. And when an approval is still pending after a drain, it **prints the
job row** — status, attempts, `run_after`, the clock, and `last_error`. Printed rather than
asserted, because several tests legitimately expect a pending approval (a refused mutation, an
expired window, a mismatched hash); captured stdout is shown for failures and hidden for passes.

The one capture obtained so far was from a test that expects failure, and showed a job claimed,
failed and rescheduled thirty seconds out. The capture that matters — a test expecting success —
has not yet occurred. **The next occurrence will carry it**, which is the proportionate answer to a
fault that reproduces about one run in six and not on demand.

This is a pre-existing suite condition rather than something CP22 introduced: it was first seen in
CP16, before the object-store tests existed.

**CP22.x investigated it directly and did not reproduce it.** Six consecutive shuffled full runs,
clean. Three structural hypotheses were tested and each is now ruled out with evidence rather than
with reasoning:

* **An RLS/GUC leak into the worker's pooled connection.** `scope_to` is transaction-local, and the
  `job` policy exempts `workos_worker` outright — which the test worker runs as. No mechanism.
* **Clock skew between the enqueuing process and the database.** `enqueue` writes `run_after` from
  the Python clock and `claim` compares it against PostgreSQL's `now()`, which is two clocks for one
  comparison. Measured: 300 enqueue-then-claim round trips, 0 unclaimable; and 1,399 samples taken
  *while the suite was running* put the Python clock consistently **behind** the database — worst
  case −0.17 ms, never once in the dangerous direction. Not the cause here, but see the risk below.
* **A row lock held between the request's commit and its session closing.** 200 trials with the
  connection deliberately left open after `COMMIT`: the worker claimed the row every time.

**A latent risk found on the way, not fixed.** `run_after` really is written from one clock and read
against another. On this machine the skew points the safe way; on separate hosts with ordinary NTP
error it may not, and the failure would be a job invisible to the claim until the clocks converge.
Setting `run_after` from the database clock would remove the race by construction. It is left alone
deliberately: changing production code on a hypothesis that the measurements do not support is how a
real cause gets buried under a plausible one. Test isolation was *not* restructured — `work_org` already gives each
test its own organization, and rebuilding isolation for a defect that would not reproduce would be
changing a working design on a hypothesis.

### Future signals available from data that already exists

Noted while building CP15–CP17, not implemented, and not a commitment to implement:

- **Risk**: an `open` commitment whose `due_date` is near and whose fulfilling Work is `blocked`
  is a deterministic at-risk signal needing no new context.
- **Bottleneck**: a Person holding several `open` commitments due in one week, or a Work item
  blocking several others, are both single queries over existing columns.
- **Health**: the ratio of `fulfilled` to `missed` per person or team is already computable; it
  needs a decision about what it would be *used for* before it is worth surfacing.
- **Decision**: nothing in the schema records one. This is the one that genuinely needs a context.
