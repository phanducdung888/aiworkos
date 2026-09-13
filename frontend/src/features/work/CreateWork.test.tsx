/**
 * Capture, and the two rules it is easiest to break from a form.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { CreateWork } from './CreateWork'
import { aWork, problem, renderSurface, stubApi } from '@/test/harness'

const noProjects = { match: 'GET /api/v1/projects', body: { items: [], next_cursor: null } }

describe('CreateWork', () => {
  it('captures work with nothing but a title', async () => {
    let sent: unknown
    stubApi([
      noProjects,
      {
        match: 'POST /api/v1/work',
        status: 201,
        body: aWork(),
        onRequest: ({ body }) => {
          sent = JSON.parse(body)
        },
      },
    ])
    renderSurface(<CreateWork />)

    await userEvent.type(await screen.findByLabelText(/tiêu đề/i), 'Check the IOC API')
    await userEvent.click(screen.getByRole('button', { name: /tạo công việc/i }))

    // ADR-0029, BR-W-07, BR-W-15: no project, no assignment, and neither is an error.
    await waitFor(() => expect(sent).toEqual({ title: 'Check the IOC API', project_id: null }))
  })

  it('never requires a project to submit', async () => {
    stubApi([noProjects])
    renderSurface(<CreateWork />)

    const project = await screen.findByLabelText(/dự án/i)
    expect(project).not.toBeRequired()
    await userEvent.type(screen.getByLabelText(/tiêu đề/i), 'Unparented')
    // The submit is reachable with the project still unset. A form that disabled it here would be
    // the UI path ADR-0029 forbids.
    expect(screen.getByRole('button', { name: /tạo công việc/i })).toBeEnabled()
  })

  it('sends an idempotency key so a retried capture makes one item', async () => {
    const keys: (string | undefined)[] = []
    stubApi([
      noProjects,
      {
        match: 'POST /api/v1/work',
        status: 201,
        body: aWork(),
        onRequest: ({ headers }) => {
          keys.push(headers.get('Idempotency-Key') ?? undefined)
        },
      },
    ])
    renderSurface(<CreateWork />)
    await userEvent.type(await screen.findByLabelText(/tiêu đề/i), 'Retryable')
    await userEvent.click(screen.getByRole('button', { name: /tạo công việc/i }))
    await waitFor(() => expect(keys).toHaveLength(1))
    expect(keys[0]).toBeTruthy()
  })

  it('shows the rule id when the domain refuses', async () => {
    stubApi([
      noProjects,
      { match: 'POST /api/v1/work', status: 422, body: problem(422, { rule: 'BR-W-01' }) },
    ])
    renderSurface(<CreateWork />)
    await userEvent.type(await screen.findByLabelText(/tiêu đề/i), '   x')
    await userEvent.click(screen.getByRole('button', { name: /tạo công việc/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('BR-W-01')
  })

  it('has no accessibility violations', async () => {
    stubApi([noProjects])
    const { container } = renderSurface(<CreateWork />)
    await screen.findByLabelText(/tiêu đề/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
