/**
 * The one way this application talks to the API.
 *
 * Typed from `backend/openapi.json` via `openapi-typescript`, so a backend field that is renamed or
 * removed fails `npm run typecheck` rather than producing `undefined` at runtime. Regenerating the
 * types is `npm run generate:api`; CI fails if the checked-in types have drifted from the spec.
 *
 * Two headers are attached to every request and neither is optional. The bearer token says which
 * human is calling. `X-Organization-Id` says which organization they are calling as — never
 * inferred, because a human may work for more than one and inference is correct right up until the
 * first person who does (security-model §2).
 */
import createClient, { type Middleware } from 'openapi-fetch'
import { ApiProblem } from './problem'
import type { paths } from './schema'

export interface RequestContext {
  /** Returns a fresh access token, or null when the session has gone. */
  getAccessToken: () => string | null
  getOrganizationId: () => string | null
}

let context: RequestContext = {
  getAccessToken: () => null,
  getOrganizationId: () => null,
}

export function configureApi(next: RequestContext): void {
  context = next
}

const authentication: Middleware = {
  async onRequest({ request }) {
    const token = context.getAccessToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    // A header the caller set explicitly wins. There is one call that needs this — checking an
    // organization identifier before committing to it — and overwriting it there would validate
    // whichever organization is already configured rather than the one being tried, which is
    // exactly right when nothing is configured yet and exactly wrong when somebody is switching.
    const organization = context.getOrganizationId()
    if (organization && !request.headers.has('X-Organization-Id')) {
      request.headers.set('X-Organization-Id', organization)
    }
    return request
  },
  async onResponse({ response }) {
    if (response.ok) return response
    // Every error this API produces is problem+json, including the ones the framework raises
    // (404 on an unknown route, 405 on a wrong method). Reading it here means no call site has to.
    const body = await response
      .clone()
      .json()
      .catch(() => ({}))
    throw new ApiProblem(response.status, body)
  },
}

/**
 * Same origin as the page.
 *
 * In development Vite proxies `/api`; in the pilot the reverse proxy serves both. Either way the
 * browser never makes a cross-origin request and the app never learns a second base URL. Written as
 * the origin rather than as an empty string because a relative URL is only resolvable where there
 * is a document — `new Request('/api/v1/work')` throws under Node, which is where the component
 * tests run.
 */
const sameOrigin = (): string =>
  typeof window === 'undefined' ? 'http://localhost' : window.location.origin

export const api = createClient<paths>({
  baseUrl: sameOrigin(),
  // Resolved per call rather than captured at module load. `createClient` would otherwise hold the
  // `fetch` that existed when this module was first imported, which makes the transport impossible
  // to substitute — and the transport is exactly the seam a component test needs.
  fetch: (request) => globalThis.fetch(request),
})
api.use(authentication)

/**
 * Unwrap a response whose failures already throw.
 *
 * `openapi-fetch` returns `{ data, error }`; the middleware above turns every error into a thrown
 * `ApiProblem`, so by the time a call site sees the result there is only data. This narrows the type
 * to match that fact instead of making every caller handle an `error` that cannot arrive.
 */
export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.data === undefined) {
    throw new ApiProblem(500, { detail: 'The API returned no body.' })
  }
  return result.data
}

/** An `If-Match` header from a version, matching the weak tag the API issues (`W/"3"`). */
export function ifMatch(version: number): Record<string, string> {
  return { 'If-Match': `W/"${version}"` }
}

/**
 * A key that makes one user action safe to retry.
 *
 * Generated per action rather than per request, so a retry after a timeout carries the key the
 * first attempt used and the API replays its answer instead of creating a second entity.
 */
export function idempotencyKey(): string {
  return crypto.randomUUID()
}
