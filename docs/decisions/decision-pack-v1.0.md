# Decision Pack v1.0 — Binding Decisions

Issued by: Product Owner / Solution Architect · Applied to the document set: 2026-09-11
Status: **binding**. These decisions supersede any earlier option list in this repository.

This file records the decisions as received, for traceability. It is the authoritative source for
PQ-1, PQ-2, PQ-3, A-1 and A-7. Where implementation detail conflicts with this pack, stop and report
the conflict rather than resolving it.

---

## PQ-1 — MVP capture sources · RESOLVED

Priority order:

1. **Web application (mandatory MVP path).** Manual message entry, pasted conversation, pasted
   meeting notes, pasted email or message, attached supporting evidence.
2. **OpenClaw-connected messaging.** Integrated through the `AgentRuntime` abstraction. First channel:
   **WhatsApp**. Additional channels must be addable without changing the Work Core. **Zalo is not an
   MVP dependency.**
3. **Internal system-generated Events.** Work status changes, comments, commitments, project activity,
   notifications, decisions and other internal activity are first-class Events, but are not external
   capture sources.

**Non-goals:** no Gmail, Outlook, Calendar, GitHub, GitLab, Odoo, Slack, Teams or Zalo integrations as
MVP dependencies. The adapter layer must allow them later without blocking the MVP.

> **Amended 2026-09-13 (ADR-0061).** Source 2 could not be built: ADR-0059 established that OpenClaw
> publishes no outbound delivery mechanism, so the only sanctioned *external message* source had no
> contract to build against. **Email over IMAP is now permitted as the first external connector** —
> a vendor-neutral protocol whose `Message-ID` is defined to be globally unique, which is what
> BR-E-02 needs. This permits the protocol, not a provider: the non-goals above still stand for
> Gmail- and Outlook-specific integrations. OpenClaw/WhatsApp remains sanctioned and is not to be
> integrated until a verified external interface exists.

**Acceptance:** a user pastes a realistic work conversation into the web UI and the system creates an
immutable Event, preserves the original source, extracts candidate Work/Commitment information,
creates a Proposal, attaches Evidence, allows approve/reject/edit, creates the Work/Commitment through
the Tool Gateway, and audits the complete chain.

## PQ-2 — Multi-tenancy · RESOLVED

Multi-organization by design from the beginning; a single organization in the MVP deployment is
acceptable. No single-org schema that would require later migration.

`Organization` is the tenant boundary. Org-scoped entities: Department, Team, Person, Project,
Milestone, Work, **WorkAssignment**, Commitment, Dependency, Risk, Decision, Event, Evidence,
Proposal, Notification, AIInteraction, and related audit/business records.

PostgreSQL with `org_id` where appropriate. RLS enforces isolation as defence in depth; application
authorization must also enforce tenant boundaries.

**Keycloak:** no realm per organization for the MVP. A product-level realm, with the application
resolving User → Organization membership → Role → Permissions. The domain model must not depend on
realm-per-tenant.

**Acceptance:** a test proves User A of Organization A cannot read, modify, drive AI actions against,
or infer Organization B data through search/reporting endpoints. The same isolation applies to
AI-generated actions.

## PQ-3 — Default AI autonomy · RESOLVED at Level 1–2

**Level 1 — observe and propose.** Analyse Events; extract candidate Work, Commitments, Risks and
Decisions; summarise progress; recommend actions. AI creates a Proposal rather than directly changing
important Work Core state.

**Level 2 — user-approved mutation.** After explicit user approval, AI may execute the approved action
through the Tool Gateway: create Work, update Work, create Commitment, assign Work, update status,
create Risk, create Decision. The approval must be associated with the approving user, the Proposal,
the exact proposed action, the timestamp, and the resulting mutation.

**Permitted automatic actions** (deterministic, internal, low-risk, policy-allowed, and explicitly not
AI autonomy): generating notifications, updating derived monitoring state, recalculating deterministic
risk indicators, creating internal DomainEvents, scheduling background jobs.

**Prohibited in the MVP.** AI must not autonomously delete business records, change organization
membership, grant permissions, change roles, alter authorization policy, send external messages
representing the user without explicit authorization, make irreversible business decisions, modify
financial or legal records, bypass approval, execute arbitrary SQL, or directly access PostgreSQL.

**Architecture:** autonomy is capability-based and policy-driven, modelled as
`agent role ∩ delegated human authority ∩ capability policy ∩ organization scope`. Never a global
boolean. Individual capabilities must be promotable later without redesign. Level 3+ is not an MVP
requirement; promotion requires measured offline precision, online acceptance, rejection, correction,
reversal, false-positive and false-negative rates, auditability and safety impact.

## A-1 — Event naming and semantics · RESOLVED

`Event`, `Evidence` and `DomainEvent` are three distinct concepts and must not be merged.

- **Event** — an occurrence or activity captured by the system. "What happened, or what information
  entered the system?" Immutable; preserves source, source type, actor identity where known,
  timestamp, organization, normalized content, reference to the raw payload, and ingestion metadata.
  Corrections are new Events or explicit correction mechanisms.
- **Evidence** — a specific piece of information supporting a claim, proposal, work state, commitment,
  risk or other business interpretation. May reference an Event. AI-generated business claims must be
  traceable to Evidence. Server verifies AI-generated excerpts against the source; fabricated excerpts
  are rejected.
- **DomainEvent** — an internal business state transition produced by the Work Core: `WorkCreated`,
  `WorkAssigned`, `WorkCompleted`, `CommitmentCreated`, `CommitmentCompleted`, `RiskRaised`,
  `DecisionCreated`. "What changed in the business domain?"

Worked example: a WhatsApp message "Anh Huy sẽ hoàn thành API IOC trước thứ Sáu." produces an Event →
AI interpretation → Evidence → Proposal → user approval → `CommitmentCreated` DomainEvent → Commitment
state in the Work Core. These are not one generic Event.

## A-7 — Work without Project · RESOLVED

A Work item may exist without a Project. Project is an optional organizational/context relationship,
not a mandatory parent.

Valid: `Work → Project = NULL`. Valid: `Work → Project`. Invalid: Project → Work as mandatory
ownership. A Project may contain zero or many Work items. Work may optionally reference Project,
Milestone, parent Work, Dependency, Commitment, Person/Team.

UI must make Project optional and must not force Project creation to record work. AI may suggest a
Project when evidence and confidence support it, but must not invent project membership.

## Cross-cutting rules

1. **AI does not own the Work Core.** It observes, interprets, proposes, recommends, and executes
   approved capabilities through controlled tools.
2. **AI never writes directly to PostgreSQL.** The only path is User/Event → Agent Runtime → AI →
   Tool Gateway → Authorization → Application Service → Domain rules → PostgreSQL.
3. **Every AI write must be explainable:** what AI did, on whose authority, which Proposal, which
   Evidence, which Event, which tool, what changed, when.
4. **Do not invent missing information.** Preserve uncertainty, lower confidence, create a Proposal,
   request clarification. Never manufacture people, projects, deadlines, commitments, progress, risks
   or evidence.
5. **The product remains useful without AI** when the LLM, OpenClaw, the AI worker or the provider is
   unavailable.
6. **MVP prioritises the complete value loop** (capture → evidence → interpretation → proposal →
   approval → mutation → monitoring) over breadth of connectors.
