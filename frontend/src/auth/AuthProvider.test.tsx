/**
 * Session handling, which the journeys exercise and nothing asserts.
 *
 * The E2E tests sign in by writing a user into storage, so they walk straight past the three things
 * that actually go wrong with OIDC: a returning visitor whose session is restored, a visitor coming
 * back from the identity provider with a code, and a visitor whose token has quietly expired. Each
 * of those is a different branch and each renders a different screen.
 *
 * The `UserManager` is injected rather than stubbed globally — `AuthProvider` already takes one for
 * this reason — so these tests need no network, no realm and no browser redirect.
 */
import { screen, waitFor } from '@testing-library/react'
import { render } from '@testing-library/react'
import type { User, UserManager } from 'oidc-client-ts'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthProvider'
import type { RequestContext } from '@/api/client'

/**
 * What `AuthProvider` registered with the API layer.
 *
 * Captured rather than inspected after the fact, because the registration is a pair of closures
 * over refs the provider owns; calling them is the only way to ask what a request would carry.
 */
const configured: RequestContext[] = []

vi.mock('@/api/client', () => ({
  configureApi: (context: RequestContext) => {
    configured.push(context)
  },
}))

function aUser(overrides: Partial<User> = {}): User {
  return {
    access_token: 'a-real-looking-token',
    expired: false,
    profile: { sub: 'mira.member' },
    ...overrides,
  } as User
}

interface Handlers {
  loaded?: (user: User) => void
  unloaded?: () => void
}

function aManager(behaviour: {
  getUser?: () => Promise<User | null>
  signinRedirectCallback?: () => Promise<User>
  handlers?: Handlers
}): UserManager {
  const handlers = behaviour.handlers ?? {}
  return {
    getUser: behaviour.getUser ?? (async () => null),
    signinRedirectCallback:
      behaviour.signinRedirectCallback ?? (async () => aUser()),
    signinRedirect: vi.fn(async () => undefined),
    removeUser: vi.fn(async () => undefined),
    events: {
      addUserLoaded: (fn: (user: User) => void) => {
        handlers.loaded = fn
      },
      addUserUnloaded: (fn: () => void) => {
        handlers.unloaded = fn
      },
      removeUserLoaded: vi.fn(),
      removeUserUnloaded: vi.fn(),
    },
  } as unknown as UserManager
}

function Probe() {
  const auth = useAuth()
  return (
    <>
      <span data-testid="status">{auth.status}</span>
      <span data-testid="org">{auth.organizationId ?? 'none'}</span>
      <span data-testid="error">{auth.error ?? 'none'}</span>
    </>
  )
}

function renderAuth(manager: UserManager) {
  return render(
    <AuthProvider manager={manager}>
      <Probe />
    </AuthProvider>,
  )
}

beforeEach(() => {
  configured.length = 0
  window.localStorage.clear()
  window.sessionStorage.clear()
  window.history.replaceState({}, '', '/')
})

describe('AuthProvider — session restoration', () => {
  it('restores a session that is still good', async () => {
    renderAuth(aManager({ getUser: async () => aUser() }))
    expect(await screen.findByTestId('status')).toHaveTextContent('signed-in')
  })

  it('treats a returning visitor with no session as signed out, not as an error', async () => {
    renderAuth(aManager({ getUser: async () => null }))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-out'))
    expect(screen.getByTestId('error')).toHaveTextContent('none')
  })

  it('remembers the organization across sessions, because it is a preference not an authority', async () => {
    window.localStorage.setItem('workos.organization-id', 'org-from-last-time')
    renderAuth(aManager({ getUser: async () => aUser() }))
    expect(await screen.findByTestId('org')).toHaveTextContent('org-from-last-time')
  })

  it('survives storage being unavailable', async () => {
    // A private window, or blocked site data. Not remembering is survivable; throwing is not.
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('access denied')
    })
    renderAuth(aManager({ getUser: async () => aUser() }))
    expect(await screen.findByTestId('status')).toHaveTextContent('signed-in')
    expect(screen.getByTestId('org')).toHaveTextContent('none')
    getItem.mockRestore()
  })
})

describe('AuthProvider — the callback from the identity provider', () => {
  it('completes the exchange and cleans the code out of the address bar', async () => {
    window.history.replaceState({}, '', '/auth/callback?code=abc&state=xyz')
    const exchange = vi.fn(async () => aUser())
    renderAuth(aManager({ signinRedirectCallback: exchange }))

    expect(await screen.findByTestId('status')).toHaveTextContent('signed-in')
    expect(exchange).toHaveBeenCalledOnce()
    // The authorization code must not survive in history: it is single-use and it is a credential.
    expect(window.location.pathname).toBe('/')
    expect(window.location.search).toBe('')
  })

  it('reports a failed exchange instead of looping back to the provider', async () => {
    window.history.replaceState({}, '', '/auth/callback?code=stale')
    renderAuth(
      aManager({
        signinRedirectCallback: async () => {
          throw new Error('code already redeemed')
        },
      }),
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('error'))
    expect(screen.getByTestId('error')).toHaveTextContent('code already redeemed')
  })

  it('does not run the exchange on an ordinary page load', async () => {
    const exchange = vi.fn(async () => aUser())
    renderAuth(aManager({ getUser: async () => aUser(), signinRedirectCallback: exchange }))
    await screen.findByTestId('status')
    expect(exchange).not.toHaveBeenCalled()
  })
})

describe('AuthProvider — expired and invalid sessions', () => {
  it('treats an expired token as signed out', async () => {
    renderAuth(aManager({ getUser: async () => aUser({ expired: true }) }))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-out'))
  })

  it('withholds an expired token from the API layer', async () => {
    // Rendering signed-out is half the job. The other half is that the transport stops sending the
    // dead token, and that is a different code path — `AuthProvider` hands the API layer a getter,
    // so what matters is what the getter returns, not what the screen says.
    renderAuth(aManager({ getUser: async () => aUser({ expired: true }) }))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-out'))

    const registered = configured.at(-1)
    expect(registered).toBeDefined()
    expect(registered!.getAccessToken()).toBeNull()
  })

  it('hands the API layer a live token while the session is good', async () => {
    renderAuth(aManager({ getUser: async () => aUser() }))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))
    expect(configured.at(-1)!.getAccessToken()).toBe('a-real-looking-token')
  })

  it('follows the provider when a session is renewed mid-visit', async () => {
    const handlers: Handlers = {}
    renderAuth(aManager({ getUser: async () => null, handlers }))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-out'))

    handlers.loaded?.(aUser())
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))
  })

  it('follows the provider when a session is dropped mid-visit', async () => {
    const handlers: Handlers = {}
    renderAuth(aManager({ getUser: async () => aUser(), handlers }))
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-in'))

    handlers.unloaded?.()
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-out'))
  })
})
