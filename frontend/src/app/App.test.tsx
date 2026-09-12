/**
 * The shell, and whether the product hangs together as one thing (CP18).
 *
 * Individual surfaces are tested where they live. What is only testable here is *coherence*: that
 * the application opens on the right screen, that every link in the navigation reaches a route
 * that exists, and that a path nobody wrote does not silently render a blank page.
 *
 * The last one is the reason this file exists. A link to a route that was renamed or removed is
 * invisible to a type checker and to every component test, and it is exactly the defect a product
 * grows while its surfaces are being built one at a time.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { stubApi } from '@/test/harness'
import { MEMBER, ORG, people } from '@/test/fixtures'

vi.mock('@/auth/AuthProvider', async () => {
  const actual = await vi.importActual<typeof import('@/auth/AuthProvider')>(
    '@/auth/AuthProvider',
  )
  return {
    ...actual,
    useAuth: () => ({
      status: 'signed-in' as const,
      organizationId: ORG,
      error: null,
      signIn: () => {},
      signOut: () => {},
    }),
  }
})

const me = {
  match: 'GET /api/v1/me',
  body: {
    display_name: 'Mai Tran',
    email: 'mai@example.test',
    org_id: ORG,
    person_id: MEMBER,
    roles: ['org_admin'],
  },
}

const empty = (path: string) => ({
  match: `GET /api/v1/${path}`,
  body: { items: [], next_cursor: null },
})

/** Everything any landing surface asks for, so a route renders rather than erroring. */
const quiet = [
  me,
  people,
  empty('commitments'),
  empty('commitments/overdue/today'),
  empty('proposals'),
  empty('work'),
  empty('projects'),
]

function renderAt(route: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
    </QueryClientProvider>
  )
  return render(<App />, { wrapper: Wrapper })
}

describe('App', () => {
  it('opens on what needs a person, not on a backlog', async () => {
    // A management product's first screen is the question it exists to answer.
    stubApi(quiet)
    renderAt('/')
    expect(await screen.findByRole('heading', { name: 'Attention', level: 1 })).toBeVisible()
  })

  it('offers the whole loop in the navigation', async () => {
    stubApi(quiet)
    renderAt('/')
    await screen.findByTestId('me-name')

    const nav = screen.getByRole('navigation', { name: 'Main' })
    for (const label of [
      'Attention',
      'Work',
      'Capture message',
      'Commitments',
      'Proposals',
    ]) {
      expect(within(nav).getByRole('link', { name: label })).toBeVisible()
    }
  })

  it('routes every navigation link to a surface that renders', async () => {
    // The defect this catches: a link whose route was renamed. `tsc` cannot see it, no component
    // test covers it, and the symptom is a blank page under a working header.
    for (const [path, heading] of [
      ['/attention', 'Attention'],
      ['/work', /work/i],
      ['/work/new', /capture work/i],
      ['/capture', /capture a message/i],
      ['/commitments', 'Commitments'],
      ['/proposals', 'Proposals'],
      ['/projects', /projects/i],
    ] as const) {
      stubApi(quiet)
      const { unmount } = renderAt(path)
      expect(
        await screen.findByRole('heading', { name: heading, level: 1 }),
        `${path} rendered no level-1 heading`,
      ).toBeVisible()
      unmount()
    }
  })

  it('hides project management from somebody whose roles do not reach it', async () => {
    // Guidance only — the backend re-decides every request (contract §14) — but a link that
    // always leads to 403 is a worse surface than no link.
    stubApi([{ ...me, body: { ...me.body, roles: ['member'] } }, ...quiet.slice(1)])
    renderAt('/')
    await screen.findByTestId('me-name')

    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).queryByRole('link', { name: 'Projects' })).toBeNull()
    expect(within(nav).getByRole('link', { name: 'Commitments' })).toBeVisible()
  })
})
