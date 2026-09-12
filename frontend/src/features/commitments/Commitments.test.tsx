/**
 * The promise surfaces, and the two questions they exist to answer.
 *
 * "What did people say they would do, and when?" — the list, including the deadline that CP15's
 * reader now extracts and the honest absence of one when it could not.
 *
 * "Why does WorkOS believe this?" — the provenance walk, verbatim excerpt and all.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { CommitmentDetail, CommitmentList, due } from './Commitments'
import { problem, renderSurface, stubApi } from '@/test/harness'
import {
  COMMITMENT,
  EXCERPT,
  aCommitment,
  people,
  provenanceOf,
} from '@/test/fixtures'

const list = (items: unknown[]) => ({
  match: 'GET /api/v1/commitments',
  body: { items, next_cursor: null },
})
const overdue = (items: unknown[]) => ({
  match: 'GET /api/v1/commitments/overdue/today',
  body: { items, next_cursor: null },
})
const detail = (overrides: Record<string, unknown> = {}) => ({
  match: `GET /api/v1/commitments/${COMMITMENT}`,
  body: aCommitment(overrides),
})

const renderDetail = () =>
  renderSurface(<CommitmentDetail />, {
    route: `/commitments/${COMMITMENT}`,
    path: '/commitments/:commitmentId',
  })

describe('due', () => {
  it('names the precision when the date is not exact', () => {
    expect(due(aCommitment() as never)).toBe('2026-09-18 (week)')
  })

  it('shows an exact date without qualification', () => {
    expect(due(aCommitment({ due_precision: 'exact' }) as never)).toBe('2026-09-18')
  })

  it('says why a promise has no date rather than showing a blank', () => {
    // ADR-0055. "No date" is a fact about the message, not a missing field.
    expect(due(aCommitment({ due_date: null, due_precision: 'vague' }) as never)).toBe(
      'No date (vague)',
    )
  })
})

describe('CommitmentList', () => {
  it('lists promises with their committer and deadline', async () => {
    stubApi([people, list([aCommitment()]), overdue([])])
    renderSurface(<CommitmentList />)

    const row = (await screen.findByRole('link', { name: EXCERPT })).closest('tr')!
    expect(within(row).getByText('Mai Tran')).toBeVisible()
    expect(within(row).getByText('2026-09-18 (week)')).toBeVisible()
  })

  it('can ask the overdue question instead, and says asking writes nothing', async () => {
    const { calls } = stubApi([
      people,
      list([aCommitment()]),
      overdue([aCommitment({ status: 'open', due_date: '2020-01-01' })]),
    ])
    renderSurface(<CommitmentList />)
    await screen.findByRole('link', { name: EXCERPT })

    await userEvent.click(screen.getByRole('checkbox', { name: /overdue only/i }))

    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('/commitments/overdue/today'))).toBe(true),
    )
    expect(screen.getByText(/computed on read/i)).toBeVisible()
    // BR-C-06: the read must not have performed the transition.
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('says nothing is overdue rather than showing an empty table', async () => {
    stubApi([people, list([aCommitment()]), overdue([])])
    renderSurface(<CommitmentList />)
    await screen.findByRole('link', { name: EXCERPT })
    await userEvent.click(screen.getByRole('checkbox', { name: /overdue only/i }))
    expect(await screen.findByText(/nothing is overdue/i)).toBeVisible()
  })

  it('has no accessibility violations', async () => {
    stubApi([people, list([aCommitment()]), overdue([])])
    const { container } = renderSurface(<CommitmentList />)
    await screen.findByRole('link', { name: EXCERPT })
    expect(await axe(container)).toHaveNoViolations()
  })
})

describe('CommitmentDetail', () => {
  it('shows the promise, who made it, and when it is due', async () => {
    stubApi([people, detail(), ...provenanceOf()])
    renderDetail()

    expect(await screen.findByTestId('committer')).toHaveTextContent('Mai Tran')
    expect(screen.getByTestId('status')).toHaveTextContent('captured')
    expect(screen.getByTestId('due')).toHaveTextContent('2026-09-18 (week)')
  })

  it('walks the chain back to the words somebody said', async () => {
    stubApi([people, detail(), ...provenanceOf()])
    renderDetail()

    // ADR-0056, in the order a reader needs it: the quote, the run, the action, the approver.
    expect(await screen.findByTestId('provenance-excerpt')).toHaveTextContent(EXCERPT)
    expect(await screen.findByTestId('provenance-interaction')).toHaveTextContent('gpt-4o')
    expect(screen.getByTestId('provenance-action')).toHaveTextContent('create_commitment')
    expect(screen.getByTestId('provenance-action')).toHaveTextContent('2026-09-18')
    expect(screen.getByTestId('provenance-approval')).toHaveTextContent('Mai Tran')
    expect(screen.getByTestId('provenance-approval')).toHaveTextContent('executed')
  })

  it('quotes the evidence rather than summarising it', async () => {
    stubApi([people, detail(), ...provenanceOf()])
    renderDetail()
    const quote = await screen.findByTestId('provenance-excerpt')
    expect(quote.tagName).toBe('BLOCKQUOTE')
    expect(quote).toHaveTextContent(EXCERPT)
  })

  it('says plainly when a person recorded the promise themselves', async () => {
    // No proposal produced it, so there is no chain to show — and an empty panel would read as
    // missing data rather than as a human having typed it.
    stubApi([
      people,
      detail(),
      { match: 'GET /api/v1/proposals', body: { items: [], next_cursor: null } },
    ])
    renderDetail()
    expect(await screen.findByText(/recorded directly by a person/i)).toBeVisible()
  })

  it('offers only the transitions the domain allows from here', async () => {
    stubApi([people, detail({ status: 'captured' }), ...provenanceOf()])
    renderDetail()

    // BR-C-04: captured -> open | disputed | withdrawn, and nothing else.
    await screen.findByTestId('status')
    expect(screen.getByRole('button', { name: /acknowledge/i })).toBeVisible()
    expect(screen.getByRole('button', { name: /dispute/i })).toBeVisible()
    expect(screen.queryByRole('button', { name: /mark fulfilled/i })).toBeNull()
  })

  it('offers nothing from a terminal state', async () => {
    stubApi([people, detail({ status: 'fulfilled' }), ...provenanceOf()])
    renderDetail()
    expect(await screen.findByTestId('terminal')).toHaveTextContent('fulfilled')
  })

  it('sends the version it read, so a stale transition is refused', async () => {
    let sentIfMatch: string | null = null
    let sentBody: unknown
    stubApi([
      people,
      detail(),
      ...provenanceOf(),
      {
        match: `POST /api/v1/commitments/${COMMITMENT}/status`,
        body: aCommitment({ status: 'open', version: 2 }),
        onRequest: ({ headers, body }) => {
          sentIfMatch = headers.get('If-Match')
          sentBody = JSON.parse(body)
        },
      },
    ])
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: /acknowledge/i }))

    await waitFor(() => expect(sentIfMatch).toBe('W/"1"'))
    expect(sentBody).toEqual({ target: 'open', new_due_date: null })
  })

  it('explains a refusal by rule rather than swallowing it', async () => {
    // BR-C-09. The approver is often not the committer, so this refusal is the common case and
    // has to read as "not yours to do" rather than as a failure.
    stubApi([
      people,
      detail(),
      ...provenanceOf(),
      {
        match: `POST /api/v1/commitments/${COMMITMENT}/status`,
        status: 422,
        body: problem(422, { rule: 'BR-C-09' }),
      },
    ])
    renderDetail()

    await userEvent.click(await screen.findByRole('button', { name: /acknowledge/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('BR-C-09')
  })

  it('has no accessibility violations', async () => {
    stubApi([people, detail(), ...provenanceOf()])
    const { container } = renderDetail()
    await screen.findByTestId('provenance-excerpt')
    expect(await axe(container)).toHaveNoViolations()
  })
})
