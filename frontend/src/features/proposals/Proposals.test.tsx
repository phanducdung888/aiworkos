/**
 * The review surface, and the promises it makes to a person who is about to authorise a write.
 *
 * The load-bearing tests here are the two about *exactness*: the action is shown argument by
 * argument, and the Evidence is shown verbatim. An approval screen that summarised either would
 * break the property `action_hash` exists to guarantee — that what the approver read is what the
 * worker runs (ADR-0041).
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { ProposalDetail, ProposalList, band } from './Proposals'
import { problem, renderSurface, stubApi } from '@/test/harness'

const PROPOSAL = '55555555-5555-4555-8555-555555555555'
const EVENT = '33333333-3333-4333-8333-333333333333'
const EVIDENCE = '88888888-8888-4888-8888-888888888888'
const APPROVAL = '99999999-9999-4999-8999-999999999999'
const COMMITMENT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const MEMBER = '44444444-4444-4444-8444-444444444444'
const WORK = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

const EXCERPT = 'I will send the revised quote on Friday'

const aProposal = (overrides: Record<string, unknown> = {}) => ({
  id: PROPOSAL,
  org_id: '11111111-1111-4111-8111-111111111111',
  kind: 'create',
  target_type: 'commitment',
  target_id: null,
  summary: EXCERPT,
  reason: 'A promise was made in this message.',
  status: 'pending',
  action: {
    tool: 'create_commitment',
    tool_version: 'v1',
    arguments: {
      statement: EXCERPT,
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
  reviewed_by_person_id: null,
  reviewed_at: null,
  rejection_reason: null,
  supersedes_proposal_id: null,
  changes: [],
  expires_at: '2026-09-13T09:00:00Z',
  created_at: '2026-09-12T09:00:00Z',
  version: 1,
  ...overrides,
})

const detail = (overrides: Record<string, unknown> = {}) => ({
  match: `GET /api/v1/proposals/${PROPOSAL}`,
  body: aProposal(overrides),
})

const evidence = {
  match: `GET /api/v1/evidence/${EVIDENCE}`,
  body: {
    id: EVIDENCE,
    org_id: '11111111-1111-4111-8111-111111111111',
    event_id: EVENT,
    target_type: 'commitment',
    target_id: COMMITMENT,
    assertion: 'creates',
    locator: { char_start: 0, char_end: EXCERPT.length },
    excerpt: EXCERPT,
    claim_summary: null,
    confidence: 85,
    produced_by_type: 'ai_interaction',
    produced_by_id: '77777777-7777-4777-8777-777777777777',
    superseded_by_id: null,
    version: 1,
    created_at: '2026-09-12T09:00:00Z',
  },
}

const sourceEvent = {
  match: `GET /api/v1/events/${EVENT}`,
  body: {
    id: EVENT,
    org_id: '11111111-1111-4111-8111-111111111111',
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
  },
}

const anApproval = (overrides: Record<string, unknown> = {}) => ({
  id: APPROVAL,
  org_id: '11111111-1111-4111-8111-111111111111',
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

const decision = (overrides: Record<string, unknown> = {}) => ({
  match: `POST /api/v1/proposals/${PROPOSAL}/decision`,
  status: 201,
  body: anApproval(overrides),
})

const queue = { match: `POST /api/v1/approvals/${APPROVAL}/queue`, status: 202, body: {} }

const approvalFor = (overrides: Record<string, unknown> = {}) => ({
  match: `GET /api/v1/proposals/${PROPOSAL}/approval`,
  body: anApproval(overrides),
})

const commitment = {
  match: `GET /api/v1/commitments/${COMMITMENT}`,
  body: {
    id: COMMITMENT,
    org_id: '11111111-1111-4111-8111-111111111111',
    statement: EXCERPT,
    committed_by_person_id: MEMBER,
    committed_to_person_id: null,
    committed_to_team_id: null,
    due_date: null,
    due_precision: 'vague',
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
  },
}

const renderDetail = () =>
  renderSurface(<ProposalDetail />, {
    route: `/proposals/${PROPOSAL}`,
    path: '/proposals/:proposalId',
  })

describe('band', () => {
  it('reads the same floors the backend publishes (ADR-0050)', () => {
    expect(band(85)).toBe('high')
    expect(band(84)).toBe('medium')
    expect(band(60)).toBe('medium')
    expect(band(59)).toBe('low')
    // Not "low with a score of nothing": an unassessable reading is its own state.
    expect(band(0)).toBe('unknown')
  })
})

describe('ProposalList', () => {
  it('lists what is waiting and says nothing has been created', async () => {
    stubApi([{ match: 'GET /api/v1/proposals', body: { items: [aProposal()], next_cursor: null } }])
    renderSurface(<ProposalList />)

    expect(await screen.findByRole('link', { name: EXCERPT })).toHaveAttribute(
      'href',
      `/proposals/${PROPOSAL}`,
    )
    expect(screen.getByText(/chưa có gì ở đây được tạo ra/i)).toBeVisible()
    expect(screen.getByRole('cell', { name: /high \(85%\)/i })).toBeVisible()
  })

  it('asks only for pending proposals', async () => {
    const { calls } = stubApi([
      { match: 'GET /api/v1/proposals', body: { items: [], next_cursor: null } },
    ])
    renderSurface(<ProposalList />)
    await screen.findByText(/không có đề xuất nào đang chờ/i)
    expect(calls.some((call) => call.url.includes('status=pending'))).toBe(true)
  })
})

describe('ProposalDetail', () => {
  it('shows the exact action, argument by argument', async () => {
    stubApi([detail(), evidence, sourceEvent])
    renderDetail()

    const action = await screen.findByTestId('action')
    // Every argument the hash covers, none of them summarised away.
    expect(action).toHaveTextContent('create_commitment')
    expect(action).toHaveTextContent('committed_by_person_id')
    expect(action).toHaveTextContent(MEMBER)
    expect(action).toHaveTextContent('origin_event_id')
    expect(screen.getByText(/c0ffee/)).toBeVisible()
  })

  it('names the work a proposed link points at, without hiding the identifier', async () => {
    // ADR-0073. A reviewer approving a link needs to know which work item it is; a UUID tells them
    // nothing. The identifier stays because it is what `action_hash` covers — approving one thing
    // and being shown another is the failure this screen exists to prevent.
    stubApi([
      detail({
        action: {
          tool: 'create_commitment',
          tool_version: 'v1',
          arguments: {
            statement: EXCERPT,
            committed_by_person_id: MEMBER,
            fulfilling_work_id: WORK,
          },
        },
      }),
      evidence,
      sourceEvent,
      {
        match: `GET /api/v1/work/${WORK}`,
        body: {
          id: WORK,
          org_id: '11111111-1111-4111-8111-111111111111',
          title: 'Soạn lại báo giá',
          status: 'in_progress',
          type: 'task',
          priority: 'normal',
          visibility: 'organization',
          version: 1,
        },
      },
    ])
    renderDetail()

    expect(await screen.findByTestId('linked-work')).toHaveTextContent('Soạn lại báo giá')
    expect(screen.getByTestId('action')).toHaveTextContent(WORK)
  })

  it('quotes the evidence verbatim rather than describing it', async () => {
    stubApi([detail(), evidence, sourceEvent])
    renderDetail()

    expect(await screen.findByTestId('excerpt')).toHaveTextContent(EXCERPT)
  })

  it('shows the confidence band and the deadline the approval will carry', async () => {
    stubApi([detail(), evidence, sourceEvent])
    renderDetail()

    expect(await screen.findByTestId('band')).toHaveTextContent('high')
    expect(screen.getByText(/uỷ quyền hành động này trong 24 giờ/i)).toBeVisible()
  })

  it('approves, queues the action and reports what it produced', async () => {
    const { calls } = stubApi([
      detail(),
      evidence,
      sourceEvent,
      decision(),
      queue,
      approvalFor(),
      commitment,
    ])
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: /duyệt/i }))

    // ADR-0048: approving records the decision; a worker performs it. The UI must hand it to the
    // queue rather than expect the decision response to have done anything.
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith(`/approvals/${APPROVAL}/queue`))).toBe(true),
    )
    expect(await screen.findByTestId('execution-status')).toHaveTextContent('đã thực thi')
    expect(await screen.findByTestId('created-commitment')).toHaveAttribute(
      'href',
      `/commitments/${COMMITMENT}`,
    )
  })

  it('sends the version it read, so a stale decision is refused', async () => {
    let sentIfMatch: string | null = null
    stubApi([
      detail(),
      evidence,
      sourceEvent,
      { ...decision(), onRequest: ({ headers }) => void (sentIfMatch = headers.get('If-Match')) },
      queue,
      approvalFor(),
      commitment,
    ])
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: /duyệt/i }))
    await waitFor(() => expect(sentIfMatch).toBe('W/"1"'))
  })

  it('shows a failed execution as a failure rather than as an absence', async () => {
    stubApi([
      detail(),
      evidence,
      sourceEvent,
      decision(),
      queue,
      approvalFor({
        execution_status: 'failed',
        executed_at: null,
        execution_error: 'BR-C-03: a commitment needs evidence',
        resulting_entity_type: null,
        resulting_entity_id: null,
      }),
    ])
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: /duyệt/i }))

    expect(await screen.findByTestId('execution-status')).toHaveTextContent('thất bại')
    expect(await screen.findByRole('alert')).toHaveTextContent('BR-C-03')
  })

  it.each([
    ['failed', 'thất bại'],
    ['expired', 'đã hết hạn'],
    ['pending', 'đang chờ'],
  ])(
    'never claims something was created when execution is %s (CP25)',
    async (executionStatus, shown) => {
      // §6: the UI must never show a successful execution when execution actually failed. The
      // claim that matters is "Created a commitment" — a person reads that as done, and a
      // resulting entity is the only thing entitled to produce it.
      stubApi([
        detail(),
        evidence,
        sourceEvent,
        decision(),
        queue,
        approvalFor({
          execution_status: executionStatus,
          executed_at: null,
          execution_error: executionStatus === 'failed' ? 'BR-C-03: something was refused' : null,
          resulting_entity_type: null,
          resulting_entity_id: null,
        }),
      ])
      renderDetail()

      await userEvent.click(await screen.findByRole('button', { name: /duyệt/i }))

      // The value the API uses, rendered in the reader's language (CP27).
      expect(await screen.findByTestId('execution-status')).toHaveTextContent(shown)
      expect(screen.queryByTestId('created-commitment')).toBeNull()
      expect(screen.queryByText(/Đã tạo một lời hứa/i)).toBeNull()
      expect(screen.queryByText(/Đã tạo một công việc/i)).toBeNull()
    },
  )

  it('will not reject without a reason, and sends the reason when given one', async () => {
    let sent: { decision: string; rejection_reason: string } | undefined
    stubApi([
      detail(),
      evidence,
      sourceEvent,
      { ...decision({ decision: 'rejected' }), onRequest: ({ body }) => void (sent = JSON.parse(body)) },
    ])
    renderDetail()

    const reject = await screen.findByRole('button', { name: /từ chối/i })
    expect(reject).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/lý do từ chối/i), 'Not a commitment')
    await userEvent.click(reject)

    await waitFor(() =>
      expect(sent).toEqual({ decision: 'rejected', rejection_reason: 'Not a commitment' }),
    )
  })

  it('offers no decision on a proposal that has already been decided', async () => {
    stubApi([detail({ status: 'accepted' }), evidence, sourceEvent])
    renderDetail()

    await screen.findByTestId('action')
    expect(screen.queryByRole('button', { name: /duyệt/i })).toBeNull()
  })

  it('shows the rule id when a decision is refused', async () => {
    stubApi([
      detail(),
      evidence,
      sourceEvent,
      {
        match: `POST /api/v1/proposals/${PROPOSAL}/decision`,
        status: 409,
        body: problem(409, { rule: 'BR-PR-06' }),
      },
    ])
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: /duyệt/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('BR-PR-06')
  })

  it('has no accessibility violations', async () => {
    stubApi([detail(), evidence, sourceEvent])
    const { container } = renderDetail()
    await screen.findByTestId('action')
    expect(await axe(container)).toHaveNoViolations()
  })
})
describe('ProposalDetail for work', () => {
  const WORK = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

  const workProposal = () =>
    aProposal({
      target_type: 'work',
      summary: 'check whether the delivery date still works',
      action: {
        tool: 'create_work',
        tool_version: 'v1',
        arguments: { title: 'check whether the delivery date still works' },
      },
    })

  const workRoutes = [
    { match: `GET /api/v1/proposals/${PROPOSAL}`, body: workProposal() },
    evidence,
    sourceEvent,
    { match: `POST /api/v1/proposals/${PROPOSAL}/decision`, status: 201, body: anApproval({ resulting_entity_type: 'work', resulting_entity_id: WORK }) },
    queue,
    { match: `GET /api/v1/proposals/${PROPOSAL}/approval`, body: anApproval({ resulting_entity_type: 'work', resulting_entity_id: WORK }) },
    {
      match: `GET /api/v1/work/${WORK}`,
      body: {
        id: WORK,
        org_id: '11111111-1111-4111-8111-111111111111',
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
      },
    },
  ]

  it('links to the work item an approval produced, not only to a commitment', async () => {
    // CP16. Before this, approving a Work proposal showed an execution status and then nothing —
    // the thing the approval created had no way back into the product.
    stubApi(workRoutes)
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: /duyệt/i }))

    expect(await screen.findByTestId('created-work')).toHaveAttribute('href', `/work/${WORK}`)
    expect(screen.getByTestId('created-work')).toHaveTextContent('delivery date')
  })

  it('shows a work proposal with no project, owner or deadline in it', async () => {
    stubApi(workRoutes)
    renderDetail()

    const action = await screen.findByTestId('action')
    expect(action).toHaveTextContent('create_work')
    expect(action).not.toHaveTextContent('project_id')
    expect(action).not.toHaveTextContent('due_date')
  })
})
describe('ProposalList history (CP18)', () => {
  it('can look at what was already decided, not only at what is waiting', async () => {
    // "What did the AI propose, and what did a person do about it" is a question about history. A
    // screen that only ever showed the outstanding ones could not answer it.
    const { calls } = stubApi([
      { match: 'GET /api/v1/proposals', body: { items: [aProposal()], next_cursor: null } },
    ])
    renderSurface(<ProposalList />)
    await screen.findByRole('link', { name: EXCERPT })

    await userEvent.click(screen.getByRole('button', { name: /đã duyệt/i }))

    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('status=accepted'))).toBe(true),
    )
  })

  it('says which kind of nothing it found', async () => {
    stubApi([{ match: 'GET /api/v1/proposals', body: { items: [], next_cursor: null } }])
    renderSurface(<ProposalList />)
    expect(await screen.findByText(/không có đề xuất nào đang chờ/i)).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: /đã từ chối/i }))
    expect(await screen.findByText(/không có đề xuất nào đã từ chối/i)).toBeVisible()
  })
})

describe('ProposalDetail revisited (CP18)', () => {
  it('shows what happened when an approved proposal is opened later', async () => {
    // The defect this fixes: coming back to an approved proposal showed a status and nothing else,
    // losing the answer to "what did a human approve, and what came of it" at the moment somebody
    // asked it.
    stubApi([
      { match: `GET /api/v1/proposals/${PROPOSAL}`, body: aProposal({ status: 'accepted' }) },
      evidence,
      sourceEvent,
      approvalFor(),
      commitment,
    ])
    renderDetail()

    expect(await screen.findByTestId('execution-status')).toHaveTextContent('đã thực thi')
    expect(await screen.findByTestId('created-commitment')).toBeVisible()
    expect(screen.queryByRole('button', { name: /duyệt/i })).toBeNull()
  })

  it('says a rejected proposal created nothing, rather than failing to load an approval', async () => {
    stubApi([
      { match: `GET /api/v1/proposals/${PROPOSAL}`, body: aProposal({ status: 'rejected' }) },
      evidence,
      sourceEvent,
      // No approval exists for a rejection; the 404 is the answer.
    ])
    renderDetail()

    expect(await screen.findByTestId('no-approval')).toHaveTextContent(/không có gì được tạo ra/i)
  })
})
