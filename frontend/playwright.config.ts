/**
 * The curated journeys (testing-strategy §L6).
 *
 * Four of the nine are implementable in Phase 1 — 1, 1a, 1b and 4. The rest need Events, Proposals,
 * Commitments or Risks, none of which exist yet, and writing them against stubs would test the
 * stubs.
 *
 * Both servers are started here so a run needs one command and a database. The API is pointed at the
 * JWKS the global setup publishes into `public/`, which the dev server then serves — that is how the
 * production token path runs without Keycloak.
 */
import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const REPO = resolve(import.meta.dirname, '..')
const VENV = resolve(REPO, '.venv/bin')

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/setup/global-setup.ts',
  // Journeys share one seeded organization, so they run in order rather than racing each other
  // through the same work items.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `${VENV}/uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      cwd: resolve(REPO, 'backend'),
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: !process.env.CI,
      env: {
        PYTHONPATH: '.',
        WORKOS_OIDC_ISSUER: 'https://idp.e2e/realms/workos',
        WORKOS_OIDC_AUDIENCE: 'workos-api',
        WORKOS_OIDC_JWKS_URL: 'http://127.0.0.1:5173/.e2e-jwks.json',
        WORKOS_APP_DATABASE_URL:
          'postgresql+psycopg://workos_app:workos_app@127.0.0.1:5432/workos',
        WORKOS_DATABASE_URL:
          'postgresql+psycopg://workos_owner:workos_owner@127.0.0.1:5432/workos',
      },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
      env: {
        VITE_OIDC_AUTHORITY: 'https://idp.e2e/realms/workos',
        VITE_OIDC_CLIENT_ID: 'workos-web',
        VITE_OIDC_REDIRECT_URI: 'http://127.0.0.1:5173/auth/callback',
      },
    },
  ],
})
