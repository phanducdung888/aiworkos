/**
 * The screen that decides what agents may do, and the four things it must not get wrong.
 *
 * It must not invent the grid. It must not present an undecided cell as a decision. It must not
 * imply that any setting lets an AI write without a person. And a refusal must read as a refusal.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { AgentPolicy, MODES } from './AgentPolicy'
import { problem, renderSurface, stubApi } from '@/test/harness'
import { MEMBER, ORG, people } from '@/test/fixtures'

const aCell = (overrides: Record<string, unknown> = {}) => ({
  capability: 'extract',
  entity_type: 'work',
  action: 'create',
  mode: 'off',
  decided: false,
  reason: null,
  decided_by_person_id: null,
  updated_at: null,
  version: null,
  tools: ['create_work'],
  ...overrides,
})

const grid = (available: unknown[]) => ({
  match: 'GET /api/v1/agent-policy',
  body: { items: [], available },
})

const accepted = (onRequest?: (r: { body: string }) => void) => ({
  match: 'PUT /api/v1/agent-policy',
  body: {
    id: '01010101-0101-4101-8101-010101010101',
    org_id: ORG,
    capability: 'extract',
    entity_type: 'work',
    action: 'create',
    mode: 'level_1_propose',
    reason: null,
    decided_by_person_id: MEMBER,
    updated_at: '2026-09-13T09:00:00Z',
    version: 1,
  },
  onRequest,
})

describe('MODES', () => {
  it('stops at level two, because level three is not representable', () => {
    // BR-AI-31. Not disabled in the UI — absent from the type the API accepts.
    expect(MODES.map((option) => option.mode)).toEqual([
      'off',
      'level_1_propose',
      'level_2_approved_execution',
    ])
  })
})

describe('AgentPolicy', () => {
  it('renders the grid the server published, not a list of its own', async () => {
    stubApi([
      people,
      grid([
        aCell(),
        aCell({ entity_type: 'commitment', tools: ['create_commitment'] }),
        aCell({ entity_type: 'work_assignment', action: 'assign', tools: ['assign_work'] }),
      ]),
    ])
    renderSurface(<AgentPolicy />)

    expect(await screen.findByTestId('cell-work-create')).toBeVisible()
    expect(screen.getByTestId('cell-commitment-create')).toBeVisible()
    expect(screen.getByTestId('cell-work_assignment-assign')).toBeVisible()
  })

  it('names what a cell turns on, so the decision is about something legible', async () => {
    stubApi([people, grid([aCell()])])
    renderSurface(<AgentPolicy />)
    expect(await screen.findByTestId('cell-work-create')).toHaveTextContent('create_work')
  })

  it('distinguishes an undecided cell from one somebody turned off', async () => {
    // ADR-0047: absence is denial. "Nobody has looked" and "reviewed and declined" are different
    // facts, and only one of them means the safety review happened.
    stubApi([
      people,
      grid([
        aCell(),
        aCell({
          entity_type: 'commitment',
          tools: ['create_commitment'],
          decided: true,
          mode: 'off',
          reason: 'too noisy',
          decided_by_person_id: MEMBER,
          updated_at: '2026-09-13T09:00:00Z',
        }),
      ]),
    ])
    renderSurface(<AgentPolicy />)

    expect(await screen.findByTestId('state-work-create')).toHaveTextContent(/not decided/i)
    const declined = screen.getByTestId('state-commitment-create')
    expect(declined).toHaveTextContent('Off')
    expect(declined).toHaveTextContent('Mai Tran')
    expect(declined).toHaveTextContent('too noisy')
  })

  it('says no setting here lets an AI write without a person', async () => {
    stubApi([people, grid([aCell()])])
    renderSurface(<AgentPolicy />)
    await screen.findByTestId('cell-work-create')
    expect(screen.getByText(/level 1 lets it propose/i)).toBeVisible()
  })

  it('sets one cell, naming the cell the server published rather than a guess', async () => {
    let sent: unknown
    stubApi([people, grid([aCell()]), accepted(({ body }) => void (sent = JSON.parse(body)))])
    renderSurface(<AgentPolicy />)

    const cell = await screen.findByTestId('cell-work-create')
    await userEvent.type(within(cell).getByLabelText(/reason/i), 'approved at the safety review')
    await userEvent.click(within(cell).getByRole('button', { name: 'Propose' }))

    await waitFor(() =>
      expect(sent).toEqual({
        capability: 'extract',
        entity_type: 'work',
        action: 'create',
        mode: 'level_1_propose',
        reason: 'approved at the safety review',
      }),
    )
  })

  it('shows which mode a decided cell is in', async () => {
    stubApi([
      people,
      grid([aCell({ decided: true, mode: 'level_1_propose', decided_by_person_id: MEMBER })]),
    ])
    renderSurface(<AgentPolicy />)

    const cell = await screen.findByTestId('cell-work-create')
    expect(within(cell).getByRole('button', { name: 'Propose' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(cell).getByRole('button', { name: 'Off' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('reports a refusal as a refusal', async () => {
    // The backend decides. A non-admin reaching this screen must be told, not silently ignored.
    stubApi([
      people,
      grid([aCell()]),
      { match: 'PUT /api/v1/agent-policy', status: 403, body: problem(403) },
    ])
    renderSurface(<AgentPolicy />)

    const cell = await screen.findByTestId('cell-work-create')
    await userEvent.click(within(cell).getByRole('button', { name: 'Propose' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/do not have permission/i)
  })

  it('says plainly when the build offers nothing to decide', async () => {
    stubApi([people, grid([])])
    renderSurface(<AgentPolicy />)
    expect(await screen.findByText(/no agent capability with a tool behind it/i)).toBeVisible()
  })

  it('has no accessibility violations', async () => {
    stubApi([people, grid([aCell()])])
    const { container } = renderSurface(<AgentPolicy />)
    await screen.findByTestId('cell-work-create')
    expect(await axe(container)).toHaveNoViolations()
  })
})
