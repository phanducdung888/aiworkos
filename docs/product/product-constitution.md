# Product Constitution — AI WorkOS

Version 0.1 · Status: proposed · Owner: Solution Architecture

This document defines what AI WorkOS is, what it refuses to be, and the principles that settle
arguments. Architecture, domain model and roadmap all derive from it. If a later document conflicts
with this one, this one wins until it is explicitly amended.

---

## 1. Problem

Organizations already produce the information needed to manage work: people talk in meetings, write
messages, make promises, raise concerns, decide things, ship artifacts. Work management tools ignore
almost all of it and ask humans to re-enter a thin, stale summary by hand. The result is a familiar
set of failures:

- The tracker reflects intention, not reality, and it decays between ceremonies.
- Commitments made verbally are never recorded, so nobody is accountable and nobody is reminded.
- Risks are visible to individuals weeks before they are visible to management.
- Decisions are made, then relitigated, because the rationale was never captured.
- Status reporting is a manual re-summarisation tax paid weekly by everyone.

The cost is not administrative. It is that leadership operates on a picture of the organization that
is both incomplete and out of date, and nobody knows by how much.

## 2. Vision

**AI WorkOS turns human activity into maintained, structured, auditable organizational work state,
and turns that state into executive intelligence.**

Work, commitments, dependencies, risks and decisions are captured from what people actually do,
kept current, monitored continuously, and explained with evidence.

## 3. Product principles

### P1. The record of work is a first-class system, independent of AI
The Work Core is a complete, coherent, usable work management system on its own. AI is an
intelligence layer above it. Switching AI off degrades convenience, never correctness, and never
data integrity.

### P2. AI proposes structure; the organization owns it
The system's value is a trustworthy shared model of work. Trust requires that nothing appears in the
model that a human cannot trace, question and reverse. AI writes through controlled tools, with
provenance, under authorization, and by default in a mode humans can review.

### P3. Every assertion is evidenced
If the system says a commitment exists, a risk is rising, or work is done, it can show why: which
event, which excerpt, which person, which moment. Unevidenced AI claims are a defect.

### P4. Capture cost approaches zero
The measure of the product is how little humans have to type. Every manual field is a design failure
we accept only when inference is unsafe. Where inference is uncertain, the cost is one click, not one
form.

### P5. Detection is deterministic; interpretation is AI
"Three of five milestone dependencies slipped and the owner has no capacity left" is arithmetic. It
must be reproducible, explainable, and identical every run. "This milestone is likely to miss because
the integration team is absorbing an unplanned migration" is interpretation, and is labelled as such.

### P6. Observation is consensual and bounded
The system observes work, not people. What is captured, from where, for how long, and who can see it
is explicit, configurable, and visible to the people being observed. Surveillance affordances are
not a feature we will add later by accident.

### P7. Boring where it can be, novel where it must be
Postgres, REST, RBAC and a modular monolith. Novelty is spent on capture, evidence and intelligence,
not on infrastructure.

## 4. Primary users

| User | What they need | What the product gives them |
|---|---|---|
| Individual contributor | Not to be an administrator | Work and commitments captured from their activity; one-click confirmation |
| Team lead | An accurate picture of their team's real load and blockers | Live work state, dependency and capacity signals, drafted status |
| Project / delivery lead | Early warning, not post-mortems | Risk detection, dependency chains, commitment tracking |
| Department / executive | Cross-org truth without a reporting chain | Rollups, trend and risk intelligence, answerable questions with evidence |
| Compliance / audit | Traceability of decisions and changes | Immutable audit trail, decision records, evidence links |

## 5. What the product does

1. **Capture.** Ingest activity from configured sources and from direct human input. Normalise into
   immutable Events with retained raw payloads.
2. **Structure.** Extract candidate Work, Commitments, Dependencies, Risks and Decisions, link them
   to existing entities rather than duplicating, and attach Evidence.
3. **Maintain.** Keep state current as new activity arrives: progress, completion, re-scoping,
   renegotiated dates, ownership changes.
4. **Monitor.** Continuously evaluate deterministic conditions across the work graph and raise
   Risks and Notifications.
5. **Explain.** Answer questions about the state of work with grounded, cited answers, and produce
   rollups and narratives for people who do not live in the tool.

## 5a. MVP scope (Decision Pack v1.0, PQ-1)

The MVP proves one complete loop end to end, rather than breadth of integration:

```
Source → Event → AI extraction → Proposal → Evidence → User approval → Work/Commitment → Monitoring
```

**Capture sources, in priority order**

1. **Web application — mandatory.** Manual message entry, pasted conversation, pasted meeting notes,
   pasted email or message, and attached supporting evidence files.
2. **OpenClaw-connected messaging.** WhatsApp is the first channel, integrated through the
   `AgentRuntime` abstraction. Further channels must be addable without changing the Work Core.
3. **Internal system-generated Events.** Work status changes, comments, commitments, project activity,
   notifications and decisions produce first-class Events. These are internal activity, not external
   capture sources.

**Not in the MVP:** Gmail, Outlook, Calendar, GitHub, GitLab, Odoo, Slack, Teams and Zalo. The adapter
layer must not be shaped in a way that blocks them later, and their absence must not block the MVP.

**MVP acceptance.** A user pastes a realistic work conversation into the web UI and the system creates
an immutable Event, preserves the original source, extracts candidate Work and Commitment information,
raises a Proposal with attached Evidence, lets the user approve, edit or reject it, creates the
resulting Work or Commitment through the Tool Gateway, and audits the entire chain end to end.

## 6. Non-goals

Explicit, to prevent scope drift:

- Not a chat product, document editor, meeting recorder, CRM, HRIS or ticketing helpdesk. It consumes
  those systems' output.
- Not a performance-management or productivity-scoring system. It does not rank or rate people.
- Not an autonomous agent that executes work in external systems. v1 writes only to its own Work Core.
- Not a Gantt/resource-planning suite. Scheduling is represented, not optimised.
- Not a data lake. Raw payloads are retained for evidence, not for general analytics.
- Not real-time collaborative editing.
- Not a broad integration platform in the MVP. Connector breadth is deliberately deferred until the
  capture → approval → work loop is demonstrated (PQ-1).

## 7. Success measures

The product is working if, in a pilot organization:

| Measure | Intent | Indicative target |
|---|---|---|
| Manual entry ratio | Share of entities created by typing a form vs confirmed from AI proposal | < 30% by month 3 |
| Proposal acceptance rate | Proposals accepted without edit | > 70% |
| Proposal reversal rate | AI-originated entities deleted or undone within 7 days | < 5% |
| False-positive rate on quiet activity | Entities invented from activity containing no commitment | < 2% |
| Risk lead time | Median time between system raising a risk and the human recognising it | > 5 days positive |
| State freshness | Median age of last update on active Work | < 3 days |
| Status-report effort | Time spent preparing periodic status | reduced > 50% |

Reversal rate and false-positive rate outrank acceptance rate. A system that is eagerly wrong is
worse than one that is quietly incomplete, because it poisons the record everyone else relies on.

## 8. Constraints

- Technology direction is fixed for v1: React + TypeScript + Vite, FastAPI + Python, PostgreSQL,
  Redis, Keycloak, MinIO, OpenClaw agent runtime, Docker Compose.
- The AI must never modify the database directly.
- The system must remain useful with AI disabled.
- Initial deployment target is a single organization pilot on one host or small VM set.

## 9. Amendments

Changing this document requires an ADR. Principles P1, P2, P3 and P5 are the ones most likely to be
eroded under delivery pressure, usually by a shortcut that "just lets the agent write it directly".
Treat proposals to relax them as architecture changes, not as tickets.

## 10. Product questions

### Resolved by Decision Pack v1.0

| # | Question | Decision |
|---|---|---|
| PQ-1 | MVP capture sources | Web manual entry and paste (mandatory), OpenClaw messaging with WhatsApp first, internal system Events. No other connectors. See §5a |
| PQ-2 | Tenancy | Multi-organization capable from the start; one organization in the first deployment; shared Keycloak realm with org membership resolved in the application |
| PQ-3 | Default AI autonomy | Levels 1-2: observe-and-propose, then execute only on explicit recorded user approval |

### Still open

| # | Question | Options | Impact if wrong |
|---|---|---|---|
| PQ-4 | Workforce observation legal posture, now sharper because WhatsApp capture reaches personal devices and non-participants | (a) consent and scope controls required in v1; (b) pilot under an explicit internal policy only | Legal blocker if discovered late. **Raised in priority by PQ-1.** |
| PQ-5 | Do we model capacity/effort estimates in v1? | (a) counts and dates only; (b) lightweight per-person load; (c) full estimation | Determines quality of overload risk detection |
| PQ-6 | Natural-language Q&A in v1, or fixed rollups? | (a) fixed rollups; (b) Q&A over the work graph | Large difference in AI scope and eval cost |
| PQ-7 | Resolving external identities (WhatsApp number, handle, email) to Person | (a) admin-managed mapping; (b) automatic matching with confirmation; (c) directory sync | Attribution errors are the most damaging extraction failure. **Now MVP-critical: WhatsApp carries phone numbers, not names.** |
| N-1 | Does the MVP send outbound messages, e.g. AI asking someone to clarify a commitment on WhatsApp? | (a) inbound only, clarification happens in the web review UI; (b) outbound clarification with explicit per-message authorization | Cross-cutting Rule 4 mentions requesting clarification; PQ-3 prohibits unauthorized outbound messaging. Needs an explicit answer |
| N-2 | Which languages must extraction support at MVP quality? | (a) Vietnamese and English; (b) English only; (c) Vietnamese first | WhatsApp capture and the worked example are Vietnamese. Affects prompts, eval corpus and excerpt verification |
| N-3 | Are Work comments an MVP feature? | (a) yes, comments are a capture surface and produce internal Events; (b) no, deferred, and "comments" in PQ-1 names a future capability | The internal Events list includes comments, but no Comment entity is specified anywhere |
