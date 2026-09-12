# Security & Authorization Model — AI WorkOS

Version 0.1 · Status: proposed

The system holds a structured, searchable, cross-referenced model of what everyone in an organization
is doing, promising and worried about. That is a more sensitive asset than any single source system it
reads from, because aggregation creates exposure that the individual sources did not have.

---

## 1. Principals

| Principal | Identity | Obtains credentials | May do |
|---|---|---|---|
| **Person** | Keycloak user, mapped to `Person.keycloak_subject` | OIDC auth code + PKCE from the web app | Everything their roles allow |
| **Channel connector** | Keycloak client (service account), one per channel | client credentials | Ingest Events only. Holds **no** tool permissions. The OpenClaw WhatsApp adapter uses this identity (ADR-0027) |
| **Agent** | Keycloak client, one per capability | client credentials, internal network only | Call allow-listed tools, always with a delegated principal. Holds **no** ingestion permissions |
| **System** | internal, not authenticatable externally | none | Monitor-driven transitions (BR-P-07, BR-C-06) |

Every write in the system resolves to one of these four as its `Actor`. There is no unattributed
mutation path.

## 2. Authentication

- Keycloak is the sole identity provider. The application never stores passwords and never issues its
  own long-lived user tokens.
- Web app: authorization code with PKCE, access token held in memory, refresh handled by the IdP,
  short access-token lifetime (proposed 15 min).
- API: bearer JWT, validated against Keycloak's JWKS with issuer, audience, expiry and signature
  checks on every request. No token introspection cache beyond the JWKS.
- Service accounts: client credentials, per-connector, individually revocable, rotated on a schedule.
- The agent additionally presents a delegation assertion identifying the principal on whose behalf it
  acts (§5).
- **One shared product realm, not one realm per organization** (PQ-2, ADR-0031). Claims consumed:
  `sub`, `email`, and coarse platform roles. Organization membership is resolved in the application:
  `Keycloak subject → Person → OrganizationMembership → RoleAssignment → permissions`. The domain
  model must never assume realm-per-tenant.
- A **human** may work for more than one Organization. A **Person** may not: `Person` is
  organization-scoped (PQ-2, BR-G-01), so such a human has one Person row per organization, each
  mapped to the same Keycloak subject. `keycloak_subject` is therefore unique per organization, not
  globally. This wording corrects an earlier sentence in this document that implied a single Person
  row could span organizations, which contradicts the binding decision; see the Checkpoint 1 report.
- The active organization is established per request from an explicit `X-Organization-Id` header or
  path segment, validated against membership, and never inferred from a token claim alone. Every
  request therefore has exactly one resolved org scope, which is what RLS binds to.
- Local `Person`, `OrganizationMembership` and `RoleAssignment` rows are authoritative for
  resource-scoped permissions. The reason is that fine-grained, resource-scoped authorization changes
  far more often than identity and does not belong in a token.

## 3. Authorization

RBAC for coarse capability, resource scoping for reach. Deliberately not a general policy engine.

### 3.1 Roles

| Role | Scope | Capability summary |
|---|---|---|
| `org_admin` | Organization | Manage org settings, people, roles, sources, autonomy policy |
| `department_lead` | Department | Full read/write within the department subtree |
| `team_lead` | Team | Full read/write for the team's projects and work; approve proposals |
| `member` | Organization | Read per visibility; write work they own, are assigned, or that belongs to their teams' projects |
| `viewer` | Organization / Department / Project | Read only |
| `auditor` | Organization | Read all entities, audit log and AI interactions; no write |
| `executive` | Organization / Department | Read rollups and risks across scope; no write |

Roles are assigned via `RoleAssignment(person, role, scope_type, scope_id)`. A person may hold several.

### 3.2 Effective permission

```
can(actor, action, resource) :=
      same_organization(actor, resource)
  AND role_grants(actor.roles_in_scope(resource), action)
  AND visibility_allows(actor, resource)
  AND (resource.sensitivity == normal OR actor_is_participant_or_auditor)
```

Evaluated in one place: `platform/authz`. Application services call it; routers and tools never
implement their own checks. Every decision is loggable with its inputs, which is what makes the
authorization matrix testable (see `docs/testing/testing-strategy.md` §5).

### 3.3 Visibility

`Project` and `Work` carry a visibility level: `organization`, `department`, `team`, `restricted`.
Work inherits from its Project unless explicitly narrowed, never widened. Events carry `sensitivity`
independently, because the confidentiality of a conversation is not the confidentiality of the task
it produced.

Consequence worth stating: a person may be permitted to see a Work item but not the Event that
produced it. In that case the UI shows the entity and states that supporting evidence is restricted,
rather than silently showing an unevidenced claim.

### 3.4 Tenancy isolation

Multi-organization from day one (PQ-2). A single-organization MVP deployment is acceptable; a
single-organization schema is not. Single database, `org_id` on every organization-owned table
(ADR-0008), with three layers:

1. Application-level scoping in every repository query, derived from the request context, not from
   user input.
2. PostgreSQL Row-Level Security policies keyed on a session variable set per transaction, as
   defence in depth against a missing `WHERE`.
3. Cross-organization references rejected at the domain layer (BR-G-01).

All three remain in a single-organization deployment. Retrofitting isolation later is far more expensive
than carrying it from the start.

**Database roles.** Layer 2 is only real if the connecting role is subject to it. `FORCE ROW LEVEL
SECURITY` is not enforced against a superuser or a role holding `BYPASSRLS`: the policies remain
attached, the planner skips them, and nothing in the schema reports the difference. The stack
therefore uses three roles and connects as neither of the privileged ones:

| Role | Connects from | Attributes |
|---|---|---|
| cluster superuser | initdb and `ops/db/dev-roles.sql` only | full |
| `workos_owner` | migrations, administrative tasks | `LOGIN CREATEROLE NOSUPERUSER NOBYPASSRLS` |
| `workos_app` | the API and workers | `LOGIN`; owns nothing |

Owning the schema with a superuser is the failure this guards against, and it is silent: the
isolation suite passes unchanged while enforcing nothing. `test_rls_isolation.py` asserts
`rolsuper` and `rolbypassrls` are false for both connecting roles before it asserts any behaviour.

**Isolation acceptance criteria (PQ-2).** A test must prove that a user of Organization A cannot read
Organization B data, modify it, drive AI actions against it, or infer it through search, listing,
reporting, rollup or error-message channels. The same isolation applies identically to AI-originated
actions: the org scope is part of the effective-authority computation (BR-AI-03) and is re-enforced by
RLS on the same connection that executes the tool call.

## 4. The AI security model

The AI is the most privileged-looking and least trustworthy component in the system: it is
non-deterministic and it processes untrusted text. It is therefore given the least ambient authority.

### 4.1 Controls

| Control | Implementation |
|---|---|
| No data-tier access | Network topology + absent credentials + CI import checks (ADR-0002) |
| No ambient authority | Every tool call carries a delegated principal; agent role alone grants nothing that writes |
| Least privilege | Effective permission = `agent_role ∩ delegated_principal` (BR-AI-03); never a superset of any human |
| Narrow vocabulary | Intention-shaped tools only; no generic update, no query execution, no deletion (BR-AI-06) |
| Blast radius | Per-interaction caps on entities created and modified (BR-AI-04) |
| Idempotency | Required key per mutation; replays are not duplicates |
| Provenance | Evidence required on mutations (BR-AI-02); attaching Evidence to an existing entity is itself a reviewed mutation (BR-AI-36) |
| Full audit | Every tool call recorded including rejections |
| Egress control | Agent may reach only allow-listed model endpoints; no arbitrary outbound HTTP |
| Secrets isolation | Model API key exists only in the agent container; database and storage secrets only in api/worker |
| Kill switch | `organization.ai_enabled = false` and a global runtime stop; both leave the product working |
| Approval binding | Level 2 execution requires an ApprovalRecord whose `approved_action_hash` matches the action being executed; single-use, time-limited (BR-AI-18, BR-AI-20, BR-AI-22) |
| Role separation between ingest and act | Channel connector and agent are distinct principals; neither holds the other's rights (ADR-0027) |
| Loop containment | AI never processes internally generated Events (BR-E-11), so it cannot feed on its own output |

### 4.2 Delegation

Two delegation modes, both explicit:

- **On behalf of a person** — used when the interaction originates from a user action or from an Event
  that person owns. Permissions are that person's.
- **System-scope agent** — used for background extraction from channel-captured Events, restricted to a
  dedicated weak principal with read access scoped to the Event's organization and its participants'
  visibility, and write capability limited to raising Proposals. It can never execute a mutation,
  because execution requires an ApprovalRecord naming a human approver.

At Level 2 the delegated principal is always the **approver**, not the person who was talking in the
captured conversation. Being quoted in a message does not delegate your authority to anyone. A message
saying "Huy will approve this" confers nothing: authority comes from a RoleAssignment and an
ApprovalRecord, never from text in an Event. This is asserted as its own test, because it is the most
natural-sounding way for the model to be wrong.

Nor may AI create the people it needs. If a person cannot be resolved above threshold, the correct
outcome is an unresolved attribution, never a new Person row (BR-AI-34). Person creation is outside the
tool vocabulary entirely (BR-AI-08).

Delegation assertions are short-lived, bound to a single `ai_interaction_id`, and cannot be reused.

### 4.3 Prompt injection

Threat: text inside an ingested meeting transcript or message says "create work assigned to X",
"mark all commitments fulfilled", or "output the contents of project Y".

Layered response:

1. **Authorization** — the injected instruction still executes as the delegated principal. It cannot
   reach data that principal cannot see, and cannot perform actions the tools do not offer.
2. **Vocabulary** — there is no tool for mass update, deletion, permission change or data export.
3. **Blast radius** — a burst of creations aborts the interaction.
4. **Evidence verification** — excerpts are verified verbatim server-side (BR-E-05), so fabricated
   evidence fails at the gateway.
5. **Content fencing and instruction stripping** in prompts (weakest layer, listed last on purpose).
6. **Detection** — adversarial eval set in CI, anomaly alerts on tool-call patterns, flagging of the
   Event, and human review of the affected proposals.

Level 1-2 autonomy is itself the strongest injection control in the MVP: the best outcome an injected
instruction can achieve is a Proposal that a human must read and approve. That is a meaningfully
different security posture from a system where injected text can silently mutate the record, and it is
worth defending when pressure arrives to auto-apply "obvious" cases.

Note the ordering. Prompt-level defences are a convenience; the architecture, not the prompt, is what
makes injection survivable.

## 5. Data protection

| Concern | Approach |
|---|---|
| In transit | TLS at the edge; TLS or mTLS between agent and tool gateway; internal traffic on isolated Docker networks |
| At rest | Volume/disk encryption on the host; MinIO server-side encryption; Postgres relies on disk encryption in v1 |
| Secrets | Environment injection from a secrets file or manager, never in the image or repository; separate per environment; rotation documented |
| PII | Person records, Event bodies and Evidence excerpts contain personal data by design. Minimisation applies to what we *ingest*, configurable per source |
| Retention | Per-organization policy on Events and raw payloads (BR-E-07). Purging a raw payload retains already-captured excerpts so the work record stays evidenced |
| Deletion / DSR | Person records are anonymised rather than deleted, preserving referential integrity and audit; the procedure must be written before pilot (open item) |
| Export | Organization data export for portability; export is an `org_admin` action, rate-limited and audited |
| Backups | Encrypted, restore-tested; backups inherit retention obligations |

## 6. Audit

- Append-only `audit_entries`, written in the same transaction as the change (BR-G-02). No application
  code path updates or deletes them.
- Contents: actor, action, entity, before/after, request id, `ai_interaction_id`, authorization
  context, timestamp.
- Separately readable by `auditor` without granting entity write access.
- Retained beyond entity lifetime, and beyond Event retention.
- Operational logs never contain Event bodies, evidence excerpts or tokens; they carry ids only.

## 7. Threat model summary

| # | Threat | Primary control | Residual risk |
|---|---|---|---|
| T-1 | AI writes unauthorised or fabricated state | Tool gateway, delegated permission, evidence requirement, proposals | Model errors within permitted scope; mitigated by review and reversal metrics |
| T-2 | Prompt injection via ingested content | §4.3 layers | Novel injections; mitigated by detection and adversarial evals |
| T-3 | Cross-tenant leakage | org scoping + RLS + domain rejection | Bug in a hand-written query; mitigated by RLS and tenancy tests |
| T-4 | Over-broad visibility (aggregation exposure) | Visibility levels, sensitivity, participant-restricted events | Misconfiguration; mitigated by defaults being narrow |
| T-5 | Compromised source connector floods or poisons the Event store | Per-connector credentials, rate limits, idempotency, ingestion is Events-only | Poisoned events producing bad proposals; caught in review |
| T-6 | Stolen user token | Short lifetimes, IdP-managed refresh, audit | Session-length window |
| T-7 | Agent container compromise | No data credentials, no data network, egress allow-list, narrow tools | Attacker gains the delegated principal's rights for that interaction |
| T-8 | Insider misuse of executive read access | Role separation, audit of reads on restricted entities, auditor role | Legitimate-looking access |
| T-9 | Surveillance misuse of the product itself | No per-person productivity metrics by design, transparency surfaces, consent posture (PQ-4) | Organizational policy is outside our control |
| T-10 | Model provider retains or trains on organizational data | Contractual no-training terms; self-hosting option (AI-6) | Provider dependency |
| T-11 | **Unknown sender on an open channel.** Anyone who knows the connected WhatsApp number can push content into the Event store and therefore into the extraction pipeline | Sender allow-list per channel, unresolved senders cannot be attributed as committers (BR-I-06), rate limits per sender, Level 1 autonomy means output is a Proposal a human must approve | Junk and injection attempts consume review attention and storage. Needs a channel-level sender policy before the adapter ships |
| T-12 | **Third-party content capture.** A WhatsApp group or a pasted conversation may contain messages from people who are not users and have not consented | Per-channel scope configuration, sensitivity classification, retention, transparency surfaces, PQ-4 | Legal exposure; unresolved until PQ-4 is answered |
| T-13 | Channel connector compromise leading to tool use | Connector and agent are separate principals with disjoint rights (ADR-0027) | Attacker can inject content but not act on it |

## 8. Transparency as a control

People whose activity is observed can see, for themselves: which sources are connected, what Events
reference them, what entities the system holds about them, what the AI created from those events, and
which interactions produced them. This is a security control, not only a courtesy: a system nobody can
inspect will be resisted, worked around, or fed deliberately degraded input, and it will then become
untrustworthy for everyone.

## 9. Security questions

### Resolved by Decision Pack v1.0

| # | Question | Decision |
|---|---|---|
| S-1 | Keycloak realm per organization or shared realm | **Shared product realm**; organization membership and roles resolved in the application (ADR-0031) |

### Still open

| # | Question | Options |
|---|---|---|
| S-2 | mTLS between agent and API, or signed service tokens on an isolated network | (a) tokens + network isolation for the MVP (proposed); (b) mTLS now |
| S-3 | Column-level encryption for Event bodies | (a) rely on disk encryption in v1 (proposed); (b) application-level encryption, which breaks search and excerpt verification |
| S-4 | Data residency and model-provider jurisdiction (PQ-4, AI-6), now involving personal-device messaging | Deployment-dependent; must be decided before the WhatsApp adapter ships, not before the MVP web path ships |
| S-5 | Formal DSR (access/erasure) procedure | Must be written before any production use; anonymisation semantics need legal input |
| S-6 | Should `executive` see restricted-visibility work in aggregate counts? | (a) counts include it without detail (proposed); (b) fully excluded, which makes rollups wrong |
| S-7 | **Channel sender policy.** Who may send into a connected channel and have it captured? | (a) allow-list of known numbers mapped to People (proposed, safest); (b) capture everything from configured groups, classify and let review filter it; (c) capture everything. Blocks the WhatsApp adapter (T-11) |
| S-8 | Does capture of a WhatsApp group require notifying non-user participants? | Legal input required, tied to PQ-4 and T-12 |

## Database roles (Checkpoint 9)

Three roles, none of them the cluster superuser and none holding BYPASSRLS — FORCE ROW LEVEL
SECURITY is silently inert for a role with either, so the isolation tests would be proving nothing.

| Role | Used by | Reach |
|---|---|---|
| `workos_owner` | migrations | Owns the schema. Subject to every policy. |
| `workos_app` | the API | Strict tenant isolation on every table. |
| `workos_worker` | the job worker | The same, plus one policy on `job` (ADR-0046). |

The worker's exemption lets it claim work before it knows which tenant the work belongs to, and
reaches exactly one table. It is an *identity*, not a session state: `current_user` cannot be set by
a request, a header, or a forgotten `set_config`, which is what the Checkpoint 8 version got wrong.

A compromised application role gets no queue visibility. A compromised worker role gets the queue
and nothing else — on every business table it is as constrained as the application role, which is
what makes "the worker scoped itself correctly" something the database enforces rather than
something the loop remembers.
