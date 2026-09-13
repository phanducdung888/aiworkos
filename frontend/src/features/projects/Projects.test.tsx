/**
 * Projects, and the one field the domain makes non-negotiable.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { ProjectList } from './Projects'
import { problem, renderSurface, stubApi } from '@/test/harness'

const TEAM = '66666666-6666-4666-8666-666666666666'

const teams = {
  match: 'GET /api/v1/teams',
  body: {
    items: [
      {
        id: TEAM,
        org_id: 'o',
        department_id: null,
        name: 'Platform',
        lead_person_id: null,
        status: 'active',
        created_at: '2026-09-12T00:00:00Z',
        updated_at: '2026-09-12T00:00:00Z',
        version: 1,
      },
    ],
    next_cursor: null,
  },
}

function aProject(overrides: Record<string, unknown> = {}) {
  return {
    id: '77777777-7777-4777-8777-777777777777',
    org_id: 'o',
    name: 'Migration',
    description: null,
    objective: null,
    owning_team_id: TEAM,
    department_id: null,
    lead_person_id: null,
    sponsor_person_id: null,
    status: 'proposed',
    start_date: null,
    target_date: null,
    actual_end_date: null,
    visibility: 'organization',
    source: 'human',
    created_by_person_id: null,
    created_at: '2026-09-12T00:00:00Z',
    updated_at: '2026-09-12T00:00:00Z',
    version: 1,
    ...overrides,
  }
}

describe('ProjectList', () => {
  it('requires an owning team, because the domain does', async () => {
    stubApi([{ match: 'GET /api/v1/projects', body: { items: [], next_cursor: null } }, teams])
    renderSurface(<ProjectList />)
    // BR-P-01. A form that omitted this would produce a rule violation the person could not act on.
    expect(await screen.findByLabelText(/nhóm phụ trách/i)).toBeRequired()
  })

  it('offers only teams that exist', async () => {
    stubApi([{ match: 'GET /api/v1/projects', body: { items: [], next_cursor: null } }, teams])
    renderSurface(<ProjectList />)
    const select = await screen.findByLabelText(/nhóm phụ trách/i)
    expect(within_options(select)).toEqual(['Chọn một nhóm', 'Platform'])
  })

  it('creates a project and refreshes the list from the server', async () => {
    let sent: unknown
    stubApi([
      { match: 'GET /api/v1/projects', body: { items: [], next_cursor: null } },
      teams,
      {
        match: 'POST /api/v1/projects',
        status: 201,
        body: aProject(),
        onRequest: ({ body }) => {
          sent = JSON.parse(body)
        },
      },
    ])
    renderSurface(<ProjectList />)

    await userEvent.type(await screen.findByLabelText(/^tên$/i), 'Migration')
    await userEvent.selectOptions(screen.getByLabelText(/nhóm phụ trách/i), TEAM)
    await userEvent.click(screen.getByRole('button', { name: /tạo dự án/i }))

    await waitFor(() => expect(sent).toEqual({ name: 'Migration', owning_team_id: TEAM }))
  })

  it('surfaces a refusal with its rule id', async () => {
    stubApi([
      { match: 'GET /api/v1/projects', body: { items: [], next_cursor: null } },
      teams,
      { match: 'POST /api/v1/projects', status: 422, body: problem(422, { rule: 'BR-P-01' }) },
    ])
    renderSurface(<ProjectList />)
    await userEvent.type(await screen.findByLabelText(/^tên$/i), 'Homeless')
    await userEvent.selectOptions(screen.getByLabelText(/nhóm phụ trách/i), TEAM)
    await userEvent.click(screen.getByRole('button', { name: /tạo dự án/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('BR-P-01')
  })

  it('says work does not need a project when there are none', async () => {
    stubApi([{ match: 'GET /api/v1/projects', body: { items: [], next_cursor: null } }, teams])
    renderSurface(<ProjectList />)
    // ADR-0029, phrased so an empty list does not read as something missing.
    expect(await screen.findByText(/công việc không bắt buộc phải thuộc dự án/i)).toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    stubApi([
      { match: 'GET /api/v1/projects', body: { items: [aProject()], next_cursor: null } },
      teams,
    ])
    const { container } = renderSurface(<ProjectList />)
    await screen.findByLabelText(/nhóm phụ trách/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})

function within_options(select: HTMLElement): string[] {
  return Array.from(select.querySelectorAll('option')).map((option) => option.textContent ?? '')
}
