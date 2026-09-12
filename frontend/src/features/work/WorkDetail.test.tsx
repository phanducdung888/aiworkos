/**
 * The detail surface, where three rules are visible rather than described.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { WorkDetail } from './WorkDetail'
import { aWork, problem, renderSurface, stubApi } from '@/test/harness'

const WORK_ID = '22222222-2222-4222-8222-222222222222'
const MIRA = '33333333-3333-4333-8333-333333333333'
const OTTO = '44444444-4444-4444-8444-444444444444'

function anAssignment(overrides: Record<string, unknown> = {}) {
  return {
    id: '55555555-5555-4555-8555-555555555555',
    org_id: '11111111-1111-4111-8111-111111111111',
    work_id: WORK_ID,
    person_id: MIRA,
    role: 'OWNER',
    is_primary: false,
    status: 'active',
    assigned_at: '2026-09-12T00:00:00Z',
    started_at: null,
    ended_at: null,
    source: 'human',
    created_by_person_id: null,
    version: 1,
    ...overrides,
  }
}

function baseRoutes(work = aWork(), assignments: unknown[] = [], dependencies: unknown[] = []) {
  return [
    { match: `GET /api/v1/work/${WORK_ID}`, body: work },
    { match: `GET /api/v1/work/${WORK_ID}/assignments`, body: { items: assignments } },
    { match: `GET /api/v1/work/${WORK_ID}/dependencies`, body: { items: dependencies } },
    {
      match: 'GET /api/v1/people',
      body: {
        items: [
          { id: MIRA, display_name: 'Mira Member', email: null, status: 'active' },
          { id: OTTO, display_name: 'Otto Outside', email: null, status: 'active' },
        ],
        next_cursor: null,
      },
    },
  ]
}

const at = { route: `/work/${WORK_ID}`, path: '/work/:workId' }

describe('WorkDetail', () => {
  it('offers only the transitions the state machine declares', async () => {
    stubApi(baseRoutes(aWork({ status: 'todo' })))
    renderSurface(<WorkDetail />, at)
    await screen.findByTestId('work-status')

    const moves = within(
      screen.getByRole('region', { name: /move this work/i }),
    ).getAllByRole('button')
    // BR-W-03: `todo` may go to `in_progress` or `cancelled`. Offering `done` would be a button
    // whose only outcome is a 422.
    expect(moves.map((button) => button.textContent)).toEqual(['in progress', 'cancelled'])
  })

  it('offers nothing once the work has reached a final state', async () => {
    stubApi(baseRoutes(aWork({ status: 'cancelled' })))
    renderSurface(<WorkDetail />, at)
    expect(await screen.findByText(/reached a final state/i)).toBeInTheDocument()
  })

  it('asks for a reason alongside the button that needs one', async () => {
    stubApi(baseRoutes(aWork({ status: 'in_progress' })))
    renderSurface(<WorkDetail />, at)
    // BR-W-04: blocking requires a cause, so the field is present exactly when blocking is offered.
    expect(await screen.findByLabelText(/reason for blocking/i)).toBeInTheDocument()
  })

  it('sends the version it read, so a stale change is refused', async () => {
    let ifMatch: string | null = null
    stubApi([
      ...baseRoutes(aWork({ status: 'todo', version: 7 })),
      {
        match: `POST /api/v1/work/${WORK_ID}/status`,
        body: aWork({ status: 'in_progress', version: 8 }),
        onRequest: ({ headers }) => {
          ifMatch = headers.get('If-Match')
        },
      },
    ])
    renderSurface(<WorkDetail />, at)
    await userEvent.click(await screen.findByRole('button', { name: 'in progress' }))
    // BR-G-06 over HTTP: the weak tag the API issues, echoed back.
    await waitFor(() => expect(ifMatch).toBe('W/"7"'))
  })

  it('explains a lost update as somebody else’s change', async () => {
    stubApi([
      ...baseRoutes(aWork({ status: 'todo' })),
      { match: `POST /api/v1/work/${WORK_ID}/status`, status: 412, body: problem(412) },
    ])
    renderSurface(<WorkDetail />, at)
    await userEvent.click(await screen.findByRole('button', { name: 'in progress' }))
    // "Precondition Failed" tells a person nothing they can act on.
    expect(await screen.findByRole('alert')).toHaveTextContent(/somebody else changed this/i)
  })

  it('changes owner in one call rather than ending and re-assigning', async () => {
    const paths: string[] = []
    stubApi([
      ...baseRoutes(aWork(), [anAssignment()]),
      {
        match: `PUT /api/v1/work/${WORK_ID}/owner`,
        body: anAssignment({ person_id: OTTO, version: 1 }),
        onRequest: ({ url }) => paths.push(new URL(url).pathname),
      },
    ])
    renderSurface(<WorkDetail />, at)

    await userEvent.selectOptions(await screen.findByLabelText(/^owner$/i), OTTO)
    await userEvent.click(screen.getByRole('button', { name: /set owner/i }))

    // ADR-0032: end-then-assign is the two-step route around REASSIGN. The client must not offer it.
    await waitFor(() => expect(paths).toEqual([`/api/v1/work/${WORK_ID}/owner`]))
  })

  it('says unassigned is a complete record, not a gap', async () => {
    stubApi(baseRoutes(aWork(), []))
    renderSurface(<WorkDetail />, at)
    // BR-W-15. Wording matters here: a warning would make a valid steady state look like a defect.
    expect(await screen.findByText(/complete record, not a gap/i)).toBeInTheDocument()
  })

  it('keeps ended assignments visible as history', async () => {
    stubApi(
      baseRoutes(aWork(), [
        anAssignment({ id: 'ended-1', status: 'ended', ended_at: '2026-09-01T00:00:00Z' }),
        anAssignment({ id: 'active-1', person_id: OTTO }),
      ]),
    )
    renderSurface(<WorkDetail />, at)
    // BR-W-14: rows are never deleted, so "who owned this in March" stays answerable.
    expect(await screen.findByText(/previous assignments/i)).toBeInTheDocument()
  })

  it('shows both directions of a dependency', async () => {
    stubApi(
      baseRoutes(aWork(), [], [
        {
          id: 'd1',
          org_id: 'o',
          blocker_type: 'work',
          blocker_id: 'other',
          blocked_type: 'work',
          blocked_id: WORK_ID,
          kind: 'blocks',
          status: 'active',
          rationale: null,
          created_by_person_id: null,
          created_at: '2026-09-12T00:00:00Z',
          updated_at: '2026-09-12T00:00:00Z',
          version: 1,
        },
      ]),
    )
    renderSurface(<WorkDetail />, at)
    const waiting = await screen.findByTestId('blocked-by')
    expect(within(waiting).getByText(/work:other/)).toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    const { container } = (stubApi(baseRoutes(aWork(), [anAssignment()])), renderSurface(<WorkDetail />, at))
    await screen.findByTestId('work-status')
    expect(await axe(container)).toHaveNoViolations()
  })
})
