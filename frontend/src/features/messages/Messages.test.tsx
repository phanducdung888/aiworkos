/**
 * The review surface, and the distinction it exists to preserve.
 *
 * "Nothing read this message", "something read it and found nothing" and "something tried and could
 * not" are three different answers, and an operator reviewing ingestion needs all three. A screen
 * that collapsed them would be useless in exactly the case it was built for.
 */
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MessageDetail, MessageList } from './Messages'
import { renderSurface, stubApi } from '@/test/harness'

const ORG = '11111111-1111-4111-8111-111111111111'
const EVENT = '33333333-3333-4333-8333-333333333333'
const PROPOSAL = '55555555-5555-4555-8555-555555555555'

const anEvent = (over: Record<string, unknown> = {}) => ({
  id: EVENT,
  org_id: ORG,
  type: 'EXTERNAL_MESSAGE',
  origin: 'external',
  source_system: 'email.imap',
  source_ref: 'abc@example.test',
  thread_ref: 'abc@example.test',
  content_hash: 'sha256:x',
  channel: null,
  sender_external_id: null,
  occurred_at: '2026-09-14T09:00:00Z',
  observed_at: '2026-09-14T09:00:01Z',
  title: 'Báo giá tháng 9',
  body_text: 'Tôi sẽ gửi lại báo giá trước thứ Sáu.',
  sensitivity: 'normal',
  participant_count: 2,
  processing_status: 'extracted',
  processing_error: null,
  version: 1,
  ...over,
})

const list = (items: unknown[]) => ({
  match: 'GET /api/v1/events',
  body: { items, next_cursor: null },
})

const detail = (over: Record<string, unknown> = {}) => ({
  match: `GET /api/v1/events/${EVENT}`,
  body: { ...anEvent(over), participants: [], attachments: [] },
})

const noProposals = { match: 'GET /api/v1/proposals', body: { items: [], next_cursor: null } }

describe('MessageList', () => {
  it('shows what arrived and what happened to each of them', async () => {
    stubApi([list([anEvent(), anEvent({ id: 'other', processing_status: 'failed' })])])
    renderSurface(<MessageList />)

    const table = await screen.findByTestId('messages')
    expect(table).toHaveTextContent('Báo giá tháng 9')
    expect(table).toHaveTextContent('đã phân tích')
    expect(table).toHaveTextContent('không đọc được')
  })

  it('narrows the feed by status rather than filtering in the browser', async () => {
    // The request is what carries the filter. A screen that fetched everything and hid rows would
    // report a count that leaks what it hid.
    const { calls } = stubApi([list([])])
    renderSurface(<MessageList />)
    await screen.findByTestId('no-messages')

    await userEvent.selectOptions(screen.getByLabelText('Lọc theo tình trạng'), 'failed')
    await screen.findByTestId('no-messages')

    expect(calls.some((call) => call.url.includes('processing_status=failed'))).toBe(true)
  })
})

describe('MessageDetail', () => {
  it('names what the message produced', async () => {
    stubApi([
      detail(),
      {
        match: 'GET /api/v1/proposals',
        body: {
          items: [
            { id: PROPOSAL, org_id: ORG, summary: 'Gửi lại báo giá', status: 'pending' },
          ],
          next_cursor: null,
        },
      },
    ])
    renderSurface(<MessageDetail />, { route: `/messages/${EVENT}`, path: '/messages/:eventId' })

    expect(await screen.findByTestId('produced')).toHaveTextContent('Gửi lại báo giá')
  })

  it('says that finding nothing is a result, not a failure', async () => {
    stubApi([detail(), noProposals])
    renderSurface(<MessageDetail />, { route: `/messages/${EVENT}`, path: '/messages/:eventId' })

    expect(await screen.findByTestId('nothing-found')).toBeVisible()
  })

  it('distinguishes a message nothing has read from one that was read', async () => {
    stubApi([detail({ processing_status: 'received' }), noProposals])
    renderSurface(<MessageDetail />, { route: `/messages/${EVENT}`, path: '/messages/:eventId' })

    expect(await screen.findByTestId('not-read')).toBeVisible()
    expect(screen.queryByTestId('nothing-found')).toBeNull()
  })

  it('shows why an analysis failed, so nobody has to read container logs', async () => {
    stubApi([
      detail({ processing_status: 'failed', processing_error: 'ProviderTimeout: 30s' }),
      noProposals,
    ])
    renderSurface(<MessageDetail />, { route: `/messages/${EVENT}`, path: '/messages/:eventId' })

    expect(await screen.findByTestId('processing-error')).toHaveTextContent('ProviderTimeout')
  })
})
