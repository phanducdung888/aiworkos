/**
 * Rendering a surface the way the application renders it.
 *
 * The API is stubbed at `fetch`, not at the hook layer. Stubbing hooks would test that the
 * component calls a function; stubbing the transport tests that it builds the right request, reads
 * the real response shape, and handles a real problem+json refusal — which is where the bugs are.
 */
import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { render } from '@testing-library/react'
import type { RenderResult } from '@testing-library/react'
import { vi } from 'vitest'
import { configureApi } from '@/api/client'

export interface StubbedRoute {
  /** Matched against `${method} ${pathname}`, e.g. `GET /api/v1/work`. */
  match: string
  status?: number
  body?: unknown
  /**
   * Set to assert on what the component actually sent.
   *
   * Given the constructed `Request`, not the `init` argument: the client calls `fetch(request)`
   * with a single object, so `init` is empty and reading it would silently assert nothing.
   */
  onRequest?: (request: { url: string; method: string; headers: Headers; body: string }) => void
}

export function stubApi(routes: StubbedRoute[]): { calls: { method: string; url: string }[] } {
  const calls: { method: string; url: string }[] = []

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init)
      const url = new URL(request.url, 'http://localhost')
      const key = `${request.method} ${url.pathname}`
      calls.push({ method: request.method, url: `${url.pathname}${url.search}` })

      const route = routes.find((candidate) => candidate.match === key)
      if (!route) {
        return new Response(
          JSON.stringify({
            type: 'urn:workos:error:not-found',
            title: 'Not found',
            status: 404,
            detail: 'No such resource is visible in this organization.',
          }),
          { status: 404, headers: { 'content-type': 'application/problem+json' } },
        )
      }
      if (route.onRequest) {
        const clone = request.clone()
        route.onRequest({
          url: request.url,
          method: request.method,
          headers: request.headers,
          body: request.body ? await clone.text() : '',
        })
      }
      const status = route.status ?? 200
      return new Response(JSON.stringify(route.body ?? {}), {
        status,
        headers: {
          'content-type':
            status >= 400 ? 'application/problem+json' : 'application/json',
        },
      })
    }),
  )

  configureApi({
    getAccessToken: () => 'test-token',
    getOrganizationId: () => '11111111-1111-4111-8111-111111111111',
  })

  return { calls }
}

/**
 * Render a surface at a route.
 *
 * `path` matters for any component that reads `useParams`: rendered bare, its parameters are empty
 * and it silently requests `/api/v1/work/` — a test that then fails for the wrong reason.
 */
export function renderSurface(
  ui: ReactElement,
  { route = '/', path }: { route?: string; path?: string } = {},
): RenderResult {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        {path ? <Routes><Route path={path} element={children} /></Routes> : children}
      </MemoryRouter>
    </QueryClientProvider>
  )
  return render(ui, { wrapper: Wrapper })
}

export function problem(status: number, extra: Record<string, unknown> = {}) {
  return {
    type: `urn:workos:error:${status === 403 ? 'forbidden' : 'rule-violation'}`,
    title: 'Refused',
    status,
    detail: 'The change was refused.',
    ...extra,
  }
}

export function aWork(overrides: Record<string, unknown> = {}) {
  return {
    id: '22222222-2222-4222-8222-222222222222',
    org_id: '11111111-1111-4111-8111-111111111111',
    project_id: null,
    milestone_id: null,
    parent_work_id: null,
    title: 'Check the IOC API',
    description: null,
    type: 'task',
    status: 'todo',
    priority: 'normal',
    due_date: null,
    blocked_reason: null,
    started_at: null,
    completed_at: null,
    visibility: 'team',
    source: 'human',
    created_by_person_id: null,
    created_at: '2026-09-12T00:00:00Z',
    updated_at: '2026-09-12T00:00:00Z',
    version: 1,
    ...overrides,
  }
}
