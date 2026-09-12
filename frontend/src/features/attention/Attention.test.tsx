/**
 * The management view, and the promises it makes about itself.
 *
 * Two of these tests matter more than the rest. **Nothing on this page writes** — asking whether a
 * promise is overdue must never be what makes it missed (BR-C-06). And **every number is
 * accounted for**: each section states the rule that produced it, because a figure a reader cannot
 * check is one they will either over-trust or ignore.
 */
import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { Attention, SOON_DAYS, inDays, isExpiringSoon } from './Attention'
import { renderSurface, stubApi } from '@/test/harness'
import { COMMITMENT, MEMBER, WORK, aCommitment, aProposal, aWorkItem, people } from '@/test/fixtures'

const commitments = (items: unknown[]) => ({
  match: 'GET /api/v1/commitments',
  body: { items, next_cursor: null },
})
const overdue = (items: unknown[]) => ({
  match: 'GET /api/v1/commitments/overdue/today',
  body: { items, next_cursor: null },
})
const proposals = (items: unknown[]) => ({
  match: 'GET /api/v1/proposals',
  body: { items, next_cursor: null },
})
const work = (items: unknown[]) => ({
  match: 'GET /api/v1/work',
  body: { items, next_cursor: null },
})

const quiet = [people, overdue([]), commitments([]), proposals([]), work([])]

describe('helpers', () => {
  it('looks a working week ahead', () => {
    const from = new Date('2026-09-12T09:00:00Z')
    expect(inDays(SOON_DAYS, from)).toBe('2026-09-19')
    expect(inDays(0, from)).toBe('2026-09-12')
  })

  it('calls a proposal expiring when its decision window is nearly up', () => {
    const now = new Date('2026-09-12T09:00:00Z')
    expect(isExpiringSoon(aProposal({ expires_at: '2026-09-12T11:00:00Z' }) as never, now)).toBe(
      true,
    )
    expect(isExpiringSoon(aProposal({ expires_at: '2026-09-13T09:00:00Z' }) as never, now)).toBe(
      false,
    )
  })
})

describe('Attention', () => {
  it('says so plainly when nothing needs anybody', async () => {
    stubApi(quiet)
    renderSurface(<Attention />)
    expect(await screen.findByTestId('all-clear')).toBeVisible()
  })

  it('never writes anything, however it is rendered', async () => {
    // BR-C-06. Overdue is a question. A read that performed the transition would make a status
    // change appear with no actor behind it.
    const { calls } = stubApi([
      people,
      overdue([aCommitment({ status: 'open', due_date: '2020-01-01' })]),
      commitments([aCommitment()]),
      proposals([aProposal({ status: 'pending' })]),
      work([aWorkItem({ status: 'blocked', blocked_reason: 'waiting on finance' })]),
    ])
    renderSurface(<Attention />)
    await screen.findByTestId('overdue-commitments')
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('shows overdue promises with who made them and when they were due', async () => {
    stubApi([
      people,
      overdue([aCommitment({ status: 'open', due_date: '2020-01-01', due_precision: 'exact' })]),
      commitments([]),
      proposals([]),
      work([]),
    ])
    renderSurface(<Attention />)

    const section = await screen.findByTestId('overdue-commitments')
    expect(within(section).getByRole('heading')).toHaveTextContent('Overdue promises (1)')
    expect(within(section).getByRole('link')).toHaveAttribute('href', `/commitments/${COMMITMENT}`)
    expect(section).toHaveTextContent('Mai Tran')
    expect(section).toHaveTextContent('2020-01-01')
  })

  it('accounts for every section with the rule that produced it', async () => {
    stubApi(quiet)
    renderSurface(<Attention />)
    await screen.findByTestId('all-clear')

    // A figure with no stated rule is a figure nobody can check.
    for (const testId of [
      'overdue-commitments',
      'due-soon',
      'unacknowledged',
      'pending-proposals',
      'blocked-work',
      'overdue-work',
    ]) {
      const section = screen.getByTestId(testId)
      expect(section.textContent).toMatch(/\(0\)/)
      // A stated rule, not just a heading and a count.
      expect(section.querySelector('.field__hint')?.textContent ?? '').not.toBe('')
    }
    expect(screen.getByTestId('overdue-commitments')).toHaveTextContent(/deadline nobody set/i)
  })

  it('separates promises nobody has confirmed from promises that are late', async () => {
    stubApi([
      people,
      overdue([]),
      commitments([aCommitment({ status: 'captured' })]),
      proposals([]),
      work([]),
    ])
    renderSurface(<Attention />)

    // `captured` is what the system heard, not what anybody agreed (BR-C-05) — a different kind of
    // attention from a missed deadline, so a different section.
    const section = await screen.findByTestId('unacknowledged')
    expect(section).toHaveTextContent('(1)')
    expect(screen.getByTestId('overdue-commitments')).toHaveTextContent('(0)')
  })

  it('points at proposals whose decision window is nearly up', async () => {
    const soon = new Date(Date.now() + 60 * 60 * 1000).toISOString()
    stubApi([
      people,
      overdue([]),
      commitments([]),
      proposals([aProposal({ status: 'pending', expires_at: soon })]),
      work([]),
    ])
    renderSurface(<Attention />)

    expect(await screen.findByTestId('pending-proposals-note')).toHaveTextContent(/expire within/i)
    expect(screen.getByTestId('pending-proposals')).toHaveTextContent('expires soon')
  })

  it('shows blocked work with the cause the domain requires it to have', async () => {
    stubApi([
      people,
      overdue([]),
      commitments([]),
      proposals([]),
      work([aWorkItem({ status: 'blocked', blocked_reason: 'waiting on finance' })]),
    ])
    renderSurface(<Attention />)

    const section = await screen.findByTestId('blocked-work')
    expect(within(section).getByRole('link')).toHaveAttribute('href', `/work/${WORK}`)
    expect(section).toHaveTextContent('waiting on finance')
  })

  it('does not call finished work overdue', async () => {
    // `due_before` cannot also exclude settled work in one call, so the page narrows — and a done
    // item with a past date is not something anybody needs to look at.
    stubApi([
      people,
      overdue([]),
      commitments([]),
      proposals([]),
      work([
        aWorkItem({ status: 'done', due_date: '2020-01-01' }),
        aWorkItem({ id: MEMBER, status: 'in_progress', due_date: '2020-01-01' }),
      ]),
    ])
    renderSurface(<Attention />)

    const section = await screen.findByTestId('overdue-work')
    expect(section).toHaveTextContent('(1)')
  })

  it('has no accessibility violations', async () => {
    stubApi([
      people,
      overdue([aCommitment({ status: 'open', due_date: '2020-01-01' })]),
      commitments([aCommitment()]),
      proposals([aProposal({ status: 'pending' })]),
      work([aWorkItem({ status: 'blocked', blocked_reason: 'waiting on finance' })]),
    ])
    const { container } = renderSurface(<Attention />)
    await screen.findByTestId('overdue-commitments')
    expect(await axe(container)).toHaveNoViolations()
  })
})
