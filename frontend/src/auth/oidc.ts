/**
 * OIDC authorization code with PKCE against Keycloak.
 *
 * The access token lives in memory and nowhere else. `localStorage` survives the tab, which sounds
 * convenient and means any script that runs on this origin can read it; the refresh flow exists so
 * that a page reload costs a silent round trip rather than a password (security-model §2).
 */
import { User, UserManager, WebStorageStateStore } from 'oidc-client-ts'

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `${name} is not set. Copy frontend/.env.example to .env.local — the values are not secrets.`,
    )
  }
  return value
}

export function createUserManager(): UserManager {
  return new UserManager({
    authority: required('VITE_OIDC_AUTHORITY', import.meta.env.VITE_OIDC_AUTHORITY),
    client_id: required('VITE_OIDC_CLIENT_ID', import.meta.env.VITE_OIDC_CLIENT_ID),
    redirect_uri: required('VITE_OIDC_REDIRECT_URI', import.meta.env.VITE_OIDC_REDIRECT_URI),
    response_type: 'code',
    scope: 'openid profile email',
    // Session state in sessionStorage; the token itself stays in memory inside the manager.
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: true,
    // The realm mints tokens for `workos-api` through a client scope; the SPA asks for nothing
    // extra, and the API rejects anything whose audience is not itself.
    loadUserInfo: false,
  })
}

export function accessTokenOf(user: User | null): string | null {
  if (!user || user.expired) return null
  return user.access_token
}
