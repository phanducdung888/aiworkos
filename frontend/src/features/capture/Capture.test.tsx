/**
 * Capture, and the two things this surface exists to make visible.
 *
 * One: whether a channel handle resolved to a person, because that is what decides whether a
 * promise in the message can be attributed to anybody (ADR-0054). Two: that analysing proposes and
 * creates nothing (PQ-3).
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { Capture } from './Capture'
import { problem, renderSurface, stubApi } from '@/test/harness'

const EVENT_ID = '33333333-3333-4333-8333-333333333333'
const MEMBER = '44444444-4444-4444-8444-444444444444'
const PROPOSAL = '55555555-5555-4555-8555-555555555555'

const policy = (items: unknown[] = [{ id: 'p', capability: 'extract', entity_type: 'work', action: 'create', mode: 'level_1_propose', decided_by_person_id: null, decided_at: '2026-09-12T09:00:00Z', version: 1, org_id: '11111111-1111-4111-8111-111111111111' }]) => ({
  match: 'GET /api/v1/agent-policy',
  body: { items },
})

const people = {
  match: 'GET /api/v1/people',
  body: { items: [{ id: MEMBER, display_name: 'Mai Tran', status: 'active' }], next_cursor: null },
}

const anEvent = (participants: unknown[]) => ({
  match: 'POST /api/v1/events',
  status: 201,
  body: {
    id: EVENT_ID,
    org_id: '11111111-1111-4111-8111-111111111111',
    type: 'EXTERNAL_MESSAGE',
    origin: 'external',
    source_system: 'openclaw.whatsapp',
    source_ref: null,
    sender_external_id: null,
    channel: null,
    title: null,
    body_text: 'I will send the revised quote on Friday.',
    content_hash: 'abc',
    occurred_at: '2026-09-12T09:00:00Z',
    observed_at: '2026-09-12T09:00:00Z',
    sensitivity: 'normal',
    processing_status: 'captured',
    revision_of_event_id: null,
    captured_by_person_id: null,
    participant_count: participants.length,
    participants,
    attachments: [],
    created_at: '2026-09-12T09:00:00Z',
    version: 1,
  },
})

const resolved = {
  id: '66666666-6666-4666-8666-666666666666',
  role: 'speaker',
  person_id: MEMBER,
  external_handle: '+84900000001',
  match_confidence: 95,
  resolved_at: '2026-09-12T09:00:00Z',
}

const unresolved = { ...resolved, person_id: null, match_confidence: 0, resolved_at: null }

const analysis = (proposals: string[], lowConfidence = 0) => ({
  match: `POST /api/v1/events/${EVENT_ID}/analyze`,
  status: 201,
  body: {
    ai_interaction_id: '77777777-7777-4777-8777-777777777777',
    evidence_ids: proposals.map(() => '88888888-8888-4888-8888-888888888888'),
    proposal_ids: proposals,
    low_confidence: lowConfidence,
  },
})

async function captureMessage(text = 'I will send the revised quote on Friday.') {
  await userEvent.type(await screen.findByLabelText(/^nội dung$/i), text)
  await userEvent.type(screen.getByLabelText(/định danh 1/i), '+84900000001')
  await userEvent.click(screen.getByRole('button', { name: /^ghi nhận$/i }))
}

describe('Capture', () => {
  it('captures a message with a bare channel handle and no named person', async () => {
    let sent: { participants: { person_id: string | null; external_handle: string }[] } | undefined
    stubApi([
      people,
      policy(),
      { ...anEvent([unresolved]), onRequest: ({ body }) => void (sent = JSON.parse(body)) },
    ])
    renderSurface(<Capture />)

    await captureMessage()

    await waitFor(() => expect(sent).toBeDefined())
    // BR-E-12: a participant may name a person or carry a handle. A form that demanded a person
    // would make every channel message unusable.
    expect(sent?.participants).toEqual([
      { role: 'speaker', person_id: null, external_handle: '+84900000001' },
    ])
  })

  it('shows who a confirmed handle resolved to', async () => {
    stubApi([people, policy(), anEvent([resolved])])
    renderSurface(<Capture />)

    await captureMessage()

    const cell = await screen.findByTestId('resolution')
    expect(cell).toHaveTextContent('Mai Tran')
    expect(cell).toHaveTextContent('95%')
  })

  it('says plainly when a handle resolved to nobody', async () => {
    // BR-I-06. An unvouched mapping resolves to nobody, and the reader has to be able to see that
    // rather than wonder why no commitment was proposed.
    stubApi([people, policy(), anEvent([unresolved])])
    renderSurface(<Capture />)

    await captureMessage()

    expect(await screen.findByTestId('resolution')).toHaveTextContent('Không ai')
  })

  it('analyses only when asked, and says nothing was created', async () => {
    const { calls } = stubApi([people, policy(), anEvent([resolved]), analysis([PROPOSAL])])
    renderSurface(<Capture />)

    await captureMessage()
    await screen.findByTestId('resolution')
    // Capturing must not have analysed anything: the model runs when a person asks (BR-AI-01).
    expect(calls.some((call) => call.url.includes('/analyze'))).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: /phân tích/i }))

    expect(await screen.findByText(/chưa có gì được tạo ra/i)).toBeVisible()
    expect(await screen.findByRole('link', { name: /xem đề xuất/i })).toHaveAttribute(
      'href',
      `/proposals/${PROPOSAL}`,
    )
  })

  it('explains an analysis that proposed nothing rather than showing an empty list', async () => {
    stubApi([people, policy(), anEvent([unresolved]), analysis([], 1)])
    renderSurface(<Capture />)

    await captureMessage()
    await screen.findByTestId('resolution')
    await userEvent.click(screen.getByRole('button', { name: /phân tích/i }))

    expect(await screen.findByText(/declining to guess/i)).toBeVisible()
  })

  it('shows the rule id when capture is refused', async () => {
    stubApi([
      people,
      policy(),
      { match: 'POST /api/v1/events', status: 422, body: problem(422, { rule: 'BR-E-12' }) },
    ])
    renderSurface(<Capture />)

    await captureMessage()

    expect(await screen.findByRole('alert')).toHaveTextContent('BR-E-12')
  })

  it('has no accessibility violations', async () => {
    stubApi([people, policy()])
    const { container } = renderSurface(<Capture />)
    await screen.findByLabelText(/^nội dung$/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
describe('Capture when the agent is switched off (CP18)', () => {
  it('says the organization denied it rather than blaming the model', async () => {
    // ADR-0047: absence is denial. An org that has decided nothing denies everything, and the
    // result looks identical to a model that found nothing unless somebody says which it was.
    stubApi([people, policy([]), anEvent([resolved]), analysis([], 0)])
    renderSurface(<Capture />)

    await captureMessage()
    await screen.findByTestId('resolution')
    await userEvent.click(screen.getByRole('button', { name: /phân tích/i }))

    expect(await screen.findByTestId('policy-empty')).toHaveTextContent(
      /has not granted its agents any capability/i,
    )
  })
})
