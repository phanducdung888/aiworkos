/**
 * Everything the journeys need to exist before a browser opens.
 *
 * Runs once: publish a JWKS the API will fetch, seed an organization with real people and a real
 * team, and write the ids and tokens the tests sign in with. The API and the dev server are started
 * by Playwright's `webServer`, not here, so a crash in either is reported as a server failure rather
 * than as a setup exception.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { AUDIENCE, ISSUER, mintToken, writeJwks } from './identity'

const here = dirname(fileURLToPath(import.meta.url))
export const REPO = resolve(here, '../../..')
export const JWKS_FILE = resolve(REPO, 'frontend/public/.e2e-jwks.json')
export const STATE_FILE = resolve(here, '../.state.json')

export interface E2EState {
  orgId: string
  teamId: string
  people: Record<string, string>
  tokens: Record<string, string>
  issuer: string
  audience: string
}

export default async function globalSetup(): Promise<void> {
  mkdirSync(dirname(JWKS_FILE), { recursive: true })
  await writeJwks(JWKS_FILE)

  const seeded = JSON.parse(
    execFileSync(
      resolve(REPO, '.venv/bin/python'),
      [resolve(REPO, 'ops/dev/seed.py'), '--slug', 'acme-e2e'],
      { encoding: 'utf8' },
    ),
  ) as Record<string, string>

  const subjects = ['avery.admin', 'tomas.lead', 'mira.member']
  const state: E2EState = {
    orgId: seeded.org_id!,
    teamId: seeded.team_id!,
    people: Object.fromEntries(
      subjects.map((subject) => [subject, seeded[`person:${subject}`]!]),
    ),
    tokens: Object.fromEntries(
      await Promise.all(subjects.map(async (s) => [s, await mintToken(s)] as const)),
    ),
    issuer: ISSUER,
    audience: AUDIENCE,
  }
  writeFileSync(STATE_FILE, JSON.stringify(state, null, 2))
}
