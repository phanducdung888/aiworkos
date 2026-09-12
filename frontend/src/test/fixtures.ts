/**
 * Shared response bodies, so a surface test says what it is testing rather than what a row is.
 *
 * They are whole resources on purpose. The client is generated from the real OpenAPI document and
 * the components read real fields, so a fixture missing a column is a test that passes against a
 * shape the API never sends.
 */
export const ORG = '11111111-1111-4111-8111-111111111111'
export const MEMBER = '44444444-4444-4444-8444-444444444444'
export const EVENT = '33333333-3333-4333-8333-333333333333'
export const PROPOSAL = '55555555-5555-4555-8555-555555555555'
export const EVIDENCE = '88888888-8888-4888-8888-888888888888'
export const APPROVAL = '99999999-9999-4999-8999-999999999999'
export const COMMITMENT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
export const WORK = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
export const INTERACTION = '77777777-7777-4777-8777-777777777777'

export const EXCERPT = 'I will send the revised quote by Friday'

export const people = {
  match: 'GET /api/v1/people',
  body: { items: [{ id: MEMBER, display_name: 'Mai Tran', status: 'active' }], next_cursor: null },
}

export const aCommitment = (overrides: Record<string, unknown> = {}) => ({
  id: COMMITMENT,
  org_id: ORG,
  statement: EXCERPT,
  committed_by_person_id: MEMBER,
  committed_to_person_id: null,
  committed_to_team_id: null,
  due_date: '2026-09-18',
  due_precision: 'week',
  status: 'captured',
  fulfilling_work_id: null,
  project_id: null,
  origin_event_id: EVENT,
  confidence: 85,
  acknowledged_at: null,
  previous_due_date: null,
  created_at: '2026-09-12T09:05:10Z',
  updated_at: '2026-09-12T09:05:10Z',
  version: 1,
  ...overrides,
})

export const aProposal = (overrides: Record<string, unknown> = {}) => ({
  id: PROPOSAL,
  org_id: ORG,
  kind: 'create',
  target_type: 'commitment',
  target_id: null,
  summary: EXCERPT,
  reason: 'A promise was made in this message.',
  status: 'accepted',
  action: {
    tool: 'create_commitment',
    tool_version: 'v1',
    arguments: {
      statement: EXCERPT,
      due_date: '2026-09-18',
      due_precision: 'week',
      committed_by_person_id: MEMBER,
      evidence_ids: [EVIDENCE],
      origin_event_id: EVENT,
    },
  },
  action_hash: 'c0ffee',
  confidence: 85,
  evidence_ids: [EVIDENCE],
  source_event_id: EVENT,
  routed_to_person_id: MEMBER,
  raised_by_person_id: null,
  reviewed_by_person_id: MEMBER,
  reviewed_at: '2026-09-12T09:05:00Z',
  rejection_reason: null,
  supersedes_proposal_id: null,
  changes: [],
  expires_at: '2026-09-13T09:00:00Z',
  created_at: '2026-09-12T09:00:00Z',
  version: 1,
  ...overrides,
})

export const anApproval = (overrides: Record<string, unknown> = {}) => ({
  id: APPROVAL,
  org_id: ORG,
  proposal_id: PROPOSAL,
  approver_person_id: MEMBER,
  decision: 'approved',
  approved_action: aProposal().action,
  approved_action_hash: 'c0ffee',
  edits: null,
  decided_at: '2026-09-12T09:05:00Z',
  execution_expires_at: '2026-09-13T09:05:00Z',
  execution_status: 'executed',
  executed_at: '2026-09-12T09:05:10Z',
  execution_error: null,
  resulting_entity_type: 'commitment',
  resulting_entity_id: COMMITMENT,
  version: 1,
  ...overrides,
})

export const anEvidence = (overrides: Record<string, unknown> = {}) => ({
  id: EVIDENCE,
  org_id: ORG,
  event_id: EVENT,
  target_type: 'commitment',
  target_id: COMMITMENT,
  assertion: 'creates',
  locator: { char_start: 0, char_end: EXCERPT.length },
  excerpt: EXCERPT,
  claim_summary: null,
  confidence: 85,
  produced_by_type: 'ai_interaction',
  produced_by_id: INTERACTION,
  superseded_by_id: null,
  version: 1,
  created_at: '2026-09-12T09:00:00Z',
  ...overrides,
})

export const anEvent = (overrides: Record<string, unknown> = {}) => ({
  id: EVENT,
  org_id: ORG,
  type: 'EXTERNAL_MESSAGE',
  origin: 'external',
  source_system: 'openclaw.whatsapp',
  source_ref: null,
  sender_external_id: null,
  channel: null,
  title: null,
  body_text: `${EXCERPT}.`,
  content_hash: 'abc',
  occurred_at: '2026-09-12T09:00:00Z',
  observed_at: '2026-09-12T09:00:00Z',
  sensitivity: 'normal',
  processing_status: 'captured',
  revision_of_event_id: null,
  captured_by_person_id: null,
  participant_count: 1,
  participants: [],
  attachments: [],
  created_at: '2026-09-12T09:00:00Z',
  version: 1,
  ...overrides,
})

export const anInteraction = (overrides: Record<string, unknown> = {}) => ({
  id: INTERACTION,
  org_id: ORG,
  agent_identity: 'extractor',
  runtime: 'in-house',
  kind: 'extraction',
  provider: 'openai',
  model: 'gpt-4o',
  model_version: 'gpt-4o-2024-08-06',
  prompt_id: 'extract',
  prompt_version: 'v1',
  principal_person_id: MEMBER,
  status: 'succeeded',
  error: null,
  started_at: '2026-09-12T09:00:00Z',
  finished_at: '2026-09-12T09:00:02Z',
  latency_ms: 2000,
  input_refs: {},
  output_summary: {},
  tool_calls: [],
  ...overrides,
})

/** The whole provenance walk, stubbed. Every hop ADR-0056 names. */
export const provenanceOf = (entityId: string = COMMITMENT) => [
  {
    match: 'GET /api/v1/proposals',
    body: { items: [aProposal()], next_cursor: null },
  },
  { match: `GET /api/v1/proposals/${PROPOSAL}`, body: aProposal() },
  { match: `GET /api/v1/proposals/${PROPOSAL}/approval`, body: anApproval({ resulting_entity_id: entityId }) },
  { match: `GET /api/v1/evidence/${EVIDENCE}`, body: anEvidence() },
  { match: `GET /api/v1/events/${EVENT}`, body: anEvent() },
  { match: `GET /api/v1/ai-interactions/${INTERACTION}`, body: anInteraction() },
]
/** A Work item as the AI path leaves it: no owner, no project, no deadline (ADR-0029, BR-W-15). */
export const aWorkItem = (overrides: Record<string, unknown> = {}) => ({
  id: WORK,
  org_id: ORG,
  project_id: null,
  milestone_id: null,
  parent_work_id: null,
  title: 'check whether the delivery date still works',
  description: null,
  type: 'task',
  status: 'todo',
  priority: 'normal',
  due_date: null,
  blocked_reason: null,
  started_at: null,
  completed_at: null,
  visibility: 'team',
  source: 'human',
  created_by_person_id: null,
  created_at: '2026-09-12T09:05:10Z',
  updated_at: '2026-09-12T09:05:10Z',
  version: 1,
  ...overrides,
})

/** The same walk, ending at a Work item rather than a promise. */
export const workProvenance = () => [
  {
    match: 'GET /api/v1/proposals',
    body: {
      items: [
        aProposal({
          target_type: 'work',
          summary: 'check whether the delivery date still works',
          action: {
            tool: 'create_work',
            tool_version: 'v1',
            arguments: { title: 'check whether the delivery date still works' },
          },
        }),
      ],
      next_cursor: null,
    },
  },
  {
    match: `GET /api/v1/proposals/${PROPOSAL}`,
    body: aProposal({
      target_type: 'work',
      action: {
        tool: 'create_work',
        tool_version: 'v1',
        arguments: { title: 'check whether the delivery date still works' },
      },
    }),
  },
  {
    match: `GET /api/v1/proposals/${PROPOSAL}/approval`,
    body: anApproval({ resulting_entity_type: 'work', resulting_entity_id: WORK }),
  },
  {
    match: `GET /api/v1/evidence/${EVIDENCE}`,
    body: anEvidence({
      target_type: 'work',
      target_id: WORK,
      excerpt: 'check whether the delivery date still works',
    }),
  },
  {
    match: `GET /api/v1/events/${EVENT}`,
    body: anEvent({ body_text: 'Could you check whether the delivery date still works?' }),
  },
  { match: `GET /api/v1/ai-interactions/${INTERACTION}`, body: anInteraction() },
]
