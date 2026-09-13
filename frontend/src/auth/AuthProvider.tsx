/**
 * Who is signed in, and the organization they are acting as.
 *
 * Both are needed before a single API call can be made, and they come from different places: the
 * identity provider says which human, and `/api/v1/me` says which Person that is here and what they
 * may do. The app deliberately cannot render a work surface until both have resolved, because a
 * surface rendered without them would be a surface guessing.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { User, UserManager } from 'oidc-client-ts'
import { configureApi } from '@/api/client'
import { accessTokenOf, createUserManager } from './oidc'

export type AuthStatus = 'loading' | 'signed-out' | 'signed-in' | 'error'

interface AuthValue {
  status: AuthStatus
  error: string | null
  organizationId: string | null
  setOrganizationId: (id: string) => void
  signIn: () => void
  signOut: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

/** Remembered per browser, because it is a preference and not an authority. */
const ORG_STORAGE_KEY = 'workos.organization-id'

function rememberedOrganization(): string | null {
  try {
    return window.localStorage.getItem(ORG_STORAGE_KEY)
  } catch {
    // Private windows and blocked site data both throw. The app works without the memory; the
    // person simply picks their organization again.
    return null
  }
}

export function AuthProvider({
  children,
  manager,
}: {
  children: ReactNode
  /** Injected in tests so the identity provider is not a dependency of a component test. */
  manager?: UserManager
}) {
  const managerRef = useRef<UserManager | null>(null)
  if (managerRef.current === null) {
    managerRef.current = manager ?? createUserManager()
  }
  const userManager = managerRef.current

  const [status, setStatus] = useState<AuthStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const userRef = useRef<User | null>(null)
  const [organizationId, setOrganizationIdState] = useState<string | null>(rememberedOrganization)
  const organizationRef = useRef<string | null>(organizationId)

  const setOrganizationId = useCallback((id: string) => {
    organizationRef.current = id
    setOrganizationIdState(id)
    try {
      window.localStorage.setItem(ORG_STORAGE_KEY, id)
    } catch {
      // Not remembering is survivable; failing to switch organization is not.
    }
  }, [])

  // Configured during the first render, not in an effect. Effects run after children mount, and a
  // child that starts a query on mount would have sent its first request with no credentials — a
  // 400 on the very first load that then fixes itself, which is the worst shape of bug to find.
  //
  // The API layer reads these through functions rather than being handed values, so a token
  // refreshed mid-session is picked up by the next request without re-rendering anything.
  const configured = useRef(false)
  if (!configured.current) {
    configured.current = true
    configureApi({
      getAccessToken: () => accessTokenOf(userRef.current),
      getOrganizationId: () => organizationRef.current,
    })
  }

  // The in-flight restore, shared across effect runs.
  //
  // An authorization code may be redeemed exactly once. React's StrictMode mounts, unmounts and
  // remounts in development, so the effect below runs twice — and CP25 measured the result against
  // real Keycloak: two calls to the token endpoint on every single sign-in, one of them answered
  // `400 invalid_grant: Code not valid`. It resolved in the app's favour each time only because
  // the first call happened to win the race. A `cancelled` flag does not help, because it guards
  // the state update and the damage is the second *request*.
  //
  // Holding the promise itself means the second run awaits the first rather than starting another.
  const restoring = useRef<Promise<void> | null>(null)

  useEffect(() => {
    async function restore(): Promise<void> {
      try {
        if (window.location.pathname === '/auth/callback') {
          const user = await userManager.signinRedirectCallback()
          userRef.current = user
          window.history.replaceState({}, '', '/')
          setStatus('signed-in')
          return
        }
        const user = await userManager.getUser()
        userRef.current = user
        setStatus(user && !user.expired ? 'signed-in' : 'signed-out')
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Sign-in failed.')
        setStatus('error')
      }
    }

    // Started once, and the result is applied whichever run started it.
    //
    // There is deliberately no `cancelled` guard here any more. It was doing the opposite of its
    // job: under StrictMode the first run is always "cancelled" by the remount, so guarding the
    // state update on it means the single exchange's result is thrown away and the app sits on
    // `loading` for ever. React 18 does not warn about setting state after unmount, and these
    // three writes are idempotent, so applying them unconditionally is both correct and simpler.
    restoring.current ??= restore()
    void restoring.current
    const onLoaded = (user: User) => {
      userRef.current = user
      setStatus('signed-in')
    }
    const onUnloaded = () => {
      userRef.current = null
      setStatus('signed-out')
    }
    userManager.events.addUserLoaded(onLoaded)
    userManager.events.addUserUnloaded(onUnloaded)
    return () => {
      userManager.events.removeUserLoaded(onLoaded)
      userManager.events.removeUserUnloaded(onUnloaded)
    }
  }, [userManager])

  const value = useMemo<AuthValue>(
    () => ({
      status,
      error,
      organizationId,
      setOrganizationId,
      signIn: () => void userManager.signinRedirect(),
      signOut: () => {
        userRef.current = null
        void userManager.removeUser()
        setStatus('signed-out')
      },
    }),
    [status, error, organizationId, setOrganizationId, userManager],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
