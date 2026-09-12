/**
 * Where the loop ends: the promise the approval produced, on a page a person can open.
 */
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { CommitmentDetail } from './CommitmentDetail'
import { renderSurface, stubApi } from '@/test/harness'

const COMMITMENT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const EVENT = '33333333-3333-4333-8333-333333333333'

const aCommitment = (overrides: Record<string, unknown> = {}) => ({
  match: `GET /api/v1/commitments/${COMMITMENT}`,
  body: {
    id: COMMITMENT,
    org_id: '11111111-1111-4111-8111-111111111111',
    statement: 'I will send the revised quote on Friday',
    committed_by_person_id: '44444444-4444-4444-8444-444444444444',
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
    ...overrides,
  },
})

const render = () =>
  renderSurface(<CommitmentDetail />, {
    route: `/commitments/${COMMITMENT}`,
    path: '/commitments/:commitmentId',
  })

describe('CommitmentDetail', () => {
  it('shows a promise with no deadline as a complete record', async () => {
    // BR-C-05. A conversational promise often has no date, and the precision is part of the fact
    // rather than a defect in it.
    stubApi([aCommitment()])
    render()

    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'I will send the revised quote on Friday',
    )
    expect(screen.getByText(/not stated \(vague\)/i)).toBeVisible()
  })

  it('points back at the message the promise was made in', async () => {
    stubApi([aCommitment()])
    render()

    expect(await screen.findByRole('link', { name: /source message/i })).toHaveAttribute(
      'href',
      `/proposals?event=${EVENT}`,
    )
  })

  it('has no accessibility violations', async () => {
    stubApi([aCommitment()])
    const { container } = render()
    await screen.findByRole('heading', { level: 1 })
    expect(await axe(container)).toHaveNoViolations()
  })
})
