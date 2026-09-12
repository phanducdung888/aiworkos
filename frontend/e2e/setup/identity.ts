/**
 * A signing key, a JWKS the API can fetch, and tokens the API accepts.
 *
 * Keycloak is not started for these tests, and the tokens are still real. T-3 resolved this for the
 * backend suite the same way: what is stubbed is key *distribution*, not verification. The API runs
 * its production checks — signature, issuer, audience, expiry — against a JWKS this file writes and
 * the dev server happens to serve. A token signed by the wrong key, or minted for the wrong
 * audience, is rejected here exactly as it would be in the pilot.
 *
 * The key pair is generated per run and never written to disk, so there is no test private key in
 * the repository for anybody to mistake for a real one.
 */
import { createPrivateKey, createPublicKey, generateKeyPairSync } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import { SignJWT, exportJWK } from 'jose'

export const ISSUER = 'https://idp.e2e/realms/workos'
export const AUDIENCE = 'workos-api'
export const KID = 'e2e-signing-key'

let privateKeyPem: string | null = null

export async function writeJwks(path: string): Promise<void> {
  const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
  privateKeyPem = privateKey.export({ type: 'pkcs8', format: 'pem' }).toString()

  const jwk = await exportJWK(createPublicKey(publicKey.export({ type: 'spki', format: 'pem' })))
  writeFileSync(
    path,
    JSON.stringify({ keys: [{ ...jwk, kid: KID, use: 'sig', alg: 'RS256' }] }, null, 2),
  )
}

export async function mintToken(subject: string, { expiresIn = '30m' } = {}): Promise<string> {
  if (!privateKeyPem) throw new Error('writeJwks must run before a token is minted')
  return new SignJWT({ email: `${subject}@example.test` })
    .setProtectedHeader({ alg: 'RS256', kid: KID })
    .setSubject(subject)
    .setIssuer(ISSUER)
    .setAudience(AUDIENCE)
    .setIssuedAt()
    .setExpirationTime(expiresIn)
    .sign(createPrivateKey(privateKeyPem))
}
