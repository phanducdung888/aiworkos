# Development realm

`realm.json` is imported by the Compose `keycloak` service at start-up (`start-dev
--import-realm`). It exists so the stack can be run by hand; the test suite never starts Keycloak,
it signs its own tokens with a throwaway RSA key and stubs the JWKS endpoint (testing-strategy T-3,
resolved as option (a)). Production code validates identically either way — issuer, audience,
expiry and signature.

**Nothing that is not a Keycloak field may appear in `realm.json`.** The import is strict: an
unrecognised property aborts start-up, and because `start-dev` then exits, the failure looks like
"Keycloak is broken" rather than "the realm file has a typo". A `_comment` key at the top of this
file did exactly that from Checkpoint 4 until CP24, which is why the commentary now lives here and
why `tests/architecture/test_dev_realm.py` refuses any key Keycloak does not know.

## What is in it

One realm serves every organization (PQ-2, ADR-0031). There is deliberately no organization claim
anywhere in it: membership is resolved from local rows, because a claim would keep asserting a
membership after it was revoked (security-model §2).

| Client | Flow | Purpose |
| --- | --- | --- |
| `workos-web` | public, authorization code + direct grant | The browser application |
| `workos-api` | confidential, no flows | Exists to be an audience |
| `workos-connector` | confidential, client credentials | The ingestion service account (ADR-0060) |

Each of those clients carries an audience mapper putting `workos-api` in the `aud` claim. Without
it every token would be rejected, because audience is one of the four things the API checks on
every request.

The mapper sits on the client rather than in a client scope of its own, and the realm defines no
`clientScopes` block, because **an imported `clientScopes` block replaces Keycloak's built-in
scopes rather than adding to them**. This file had one until CP24, holding a single scope, and both
clients then listed `profile`, `email` and `roles` as defaults. Those names resolved to nothing:
the realm had no `basic` scope either, so every token came back without `sub` and without `email` —
the only two claims the application reads — and the SPA asked for `openid profile email` from a
realm that had none of them.

## Subjects are pinned

Every user carries a fixed `id`, and those ids are the `keycloak_subject` values in
`ops/dev/seed.py`. They have to agree: `resolve_principal` looks up a Person by the token's `sub`,
and Keycloak issues a UUID there, not a username. The seed wrote usernames until CP24, so no real
token resolved to a Person — invisible until the stack was run end to end, because every test
signed its own token with whatever subject it had just seeded.

`service-account-workos-connector` is the connector's identity. It is a Person with the `ingestion`
role and nothing else: it may capture Events and attach to its own, and cannot read anyone else's,
approve anything, or execute a tool.

Every secret and password in `realm.json` is a development placeholder and is meant to be visible.
