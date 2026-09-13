/**
 * "Why does this work item exist?" — the Commitment answer, applied to Work (CP16).
 *
 * The same component does both, which is the assertion worth making: the chain has one shape, and
 * a second copy of the explanation would be a second place for it to drift.
 */
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { WorkDetail } from './WorkDetail'
import { renderSurface, stubApi } from '@/test/harness'
import { WORK, aWorkItem, people, workProvenance } from '@/test/fixtures'

const detail = (overrides: Record<string, unknown> = {}) => ({
  match: `GET /api/v1/work/${WORK}`,
  body: aWorkItem(overrides),
})
const noAssignments = {
  match: `GET /api/v1/work/${WORK}/assignments`,
  body: { items: [], next_cursor: null },
}
const noBlockers = {
  match: `GET /api/v1/work/${WORK}/dependencies`,
  body: { items: [], next_cursor: null },
}

const render = () =>
  renderSurface(<WorkDetail />, { route: `/work/${WORK}`, path: '/work/:workId' })

describe('WorkDetail provenance', () => {
  it('walks back to the message that asked for it', async () => {
    stubApi([people, detail(), noAssignments, noBlockers, ...workProvenance()])
    render()

    expect(await screen.findByTestId('provenance-excerpt')).toHaveTextContent(
      'check whether the delivery date still works',
    )
    expect(await screen.findByTestId('provenance-interaction')).toHaveTextContent('gpt-4o')
    expect(screen.getByTestId('provenance-action')).toHaveTextContent('create_work')
    expect(screen.getByTestId('provenance-approval')).toHaveTextContent('đã thực thi')
  })

  it('shows the exact proposed action, with the blanks still blank', async () => {
    // ADR-0029, BR-W-15: no project, no owner, no deadline. A reader should be able to see that
    // the AI asked for a title and nothing else.
    stubApi([people, detail(), noAssignments, noBlockers, ...workProvenance()])
    render()

    const action = await screen.findByTestId('provenance-action')
    expect(action).toHaveTextContent('title')
    expect(action).not.toHaveTextContent('project_id')
    expect(action).not.toHaveTextContent('due_date')
  })

  it('says plainly when a person captured the work themselves', async () => {
    stubApi([
      people,
      detail(),
      noAssignments,
      noBlockers,
      { match: 'GET /api/v1/proposals', body: { items: [], next_cursor: null } },
    ])
    render()
    expect(await screen.findByText(/do một người ghi trực tiếp/i)).toBeVisible()
  })

  it('stays quiet rather than alarming when the chain cannot be read', async () => {
    // The work item on screen is fine. A failure to explain it is a gap in what we can say, not a
    // failure of the record, and it must not compete with the page's own errors.
    stubApi([people, detail(), noAssignments, noBlockers])
    render()
    const note = await screen.findByTestId('provenance-unavailable')
    expect(note).toBeVisible()
    expect(note.closest('[role="alert"]')).toBeNull()
  })

  it('has no accessibility violations', async () => {
    stubApi([people, detail(), noAssignments, noBlockers, ...workProvenance()])
    const { container } = render()
    await screen.findByTestId('provenance-excerpt')
    expect(await axe(container)).toHaveNoViolations()
  })
})
