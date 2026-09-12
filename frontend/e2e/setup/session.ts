/**
 * Signing a browser in without an identity provider.
 *
 * `oidc-client-ts` reads the signed-in user from its configured store under a documented key, so a
 * test can put one there before the app loads. That is the whole trick — no production code learns
 * it is being tested, and no back door exists for anything else to use.
 *
 * The token is a real one, so everything past the browser is unchanged: the API verifies the
 * signature, the issuer, the audience and the expiry, then resolves the principal from local rows.
 */
import { readFileSync } from 'node:fs'
import type { Page } from '@playwright/test'
import { STATE_FILE, type E2EState } from './global-setup'

export const state = (): E2EState => JSON.parse(readFileSync(STATE_FILE, 'utf8')) as E2EState

const CLIENT_ID = 'workos-web'

export async function signIn(page: Page, subject: keyof E2EState['tokens']): Promise<void> {
  const current = state()
  const token = current.tokens[subject]
  if (!token) throw new Error(`no token seeded for ${String(subject)}`)

  await page.addInitScript(
    ({ authority, clientId, accessToken, orgId }) => {
      window.sessionStorage.setItem(
        `oidc.user:${authority}:${clientId}`,
        JSON.stringify({
          access_token: accessToken,
          token_type: 'Bearer',
          scope: 'openid profile email',
          profile: { sub: 'e2e' },
          expires_at: Math.floor(Date.now() / 1000) + 1800,
        }),
      )
      // The organization is a choice the app remembers, never something it infers.
      window.localStorage.setItem('workos.organization-id', orgId)
    },
    {
      authority: current.issuer,
      clientId: CLIENT_ID,
      accessToken: token,
      orgId: current.orgId,
    },
  )
}
