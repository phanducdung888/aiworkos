/**
 * The work list, including the two claims BR-RPT makes about counting.
 */
import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { axe } from 'vitest-axe'
import { WorkList } from './WorkList'
import { aWork, problem, renderSurface, stubApi } from '@/test/harness'

describe('WorkList', () => {
  it('states which partition each figure covers', async () => {
    stubApi([
      {
        match: 'GET /api/v1/work',
        body: {
          items: [
            aWork({ id: 'a', title: 'Unparented' }),
            aWork({ id: 'b', title: 'In a project', project_id: 'p1' }),
            aWork({ id: 'c', title: 'Also unparented' }),
          ],
          next_cursor: null,
        },
      },
    ])
    renderSurface(<WorkList />)

    // BR-RPT-01 and BR-RPT-04: All Work is the sum of the two partitions, and each figure says
    // which one it covers. A single total would be the ambiguous number the rule forbids.
    expect(await screen.findByTestId('count-total')).toHaveTextContent('3')
    expect(screen.getByTestId('count-project')).toHaveTextContent('1')
    expect(screen.getByTestId('count-non-project')).toHaveTextContent('2')
  })

  it('says so when there is nothing, rather than rendering an empty table', async () => {
    stubApi([{ match: 'GET /api/v1/work', body: { items: [], next_cursor: null } }])
    renderSurface(<WorkList />)
    expect(await screen.findByText(/no work matches this view/i)).toBeInTheDocument()
  })

  it('distinguishes "you may not see this" from "something broke"', async () => {
    stubApi([{ match: 'GET /api/v1/work', status: 403, body: problem(403) }])
    renderSurface(<WorkList />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/do not have permission/i)
    // A generic failure would invite a retry; a refusal is an answer and must not.
    expect(screen.queryByRole('button', { name: /try again/i })).not.toBeInTheDocument()
  })

  it('offers a retry when the failure really might be transient', async () => {
    stubApi([{ match: 'GET /api/v1/work', status: 500, body: problem(500) }])
    renderSurface(<WorkList />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
  })

  it('has no accessibility violations on the populated surface', async () => {
    stubApi([
      { match: 'GET /api/v1/work', body: { items: [aWork()], next_cursor: null } },
    ])
    const { container } = renderSurface(<WorkList />)
    await screen.findByRole('table')
    expect(await axe(container)).toHaveNoViolations()
  })
})
