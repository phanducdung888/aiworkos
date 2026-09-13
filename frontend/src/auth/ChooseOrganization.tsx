/**
 * Naming the organization to work in.
 *
 * The app has always said "Choose the organization you are working in." and, until CP25, offered
 * nothing to choose with: `setOrganizationId` existed and no component called it. A real sign-in
 * therefore ended on a sentence, having made no API call at all.
 *
 * **The identifier is typed, not listed, and that is the design rather than a shortcut.** Which
 * organizations a person belongs to is a question no signed-in caller can ask here: the token
 * carries no organization claim on purpose (security-model §2 — a claim keeps asserting a
 * membership after it is revoked), and every table that could answer it is under RLS keyed on
 * `app_current_org()`, so a session with no organization context sees nothing anywhere. Answering
 * it would need either a claim or a `BYPASSRLS` role, and both reverse a decision this system made
 * deliberately (ADR-0008, ADR-0031). `app/platform/principal.py` already states the rule this
 * implements: the organization arrives explicitly and is never inferred, "not from a single
 * membership, not from a default".
 *
 * What it must not do is fail silently. An identifier that is not yours produces a 404 on every
 * subsequent request, which reads as an empty product rather than as a wrong answer — so it is
 * checked here, once, against `/api/v1/me`, and only stored if it resolves.
 */
import { useState } from 'react'
import { api } from '@/api/client'
import { ApiProblem } from '@/api/problem'
import { Field } from '@/components/Field'
import { useAuth } from '@/auth/AuthProvider'

/** What went wrong, in the words of the person who typed it. */
function explain(error: unknown): string {
  if (error instanceof ApiProblem) {
    if (error.status === 404) {
      return 'No organization with that identifier has you as an active member. Check the identifier, or ask whoever set up your account.'
    }
    if (error.status === 403) {
      return 'You are a member of that organization but hold no role in it yet. Somebody with administrator rights has to grant you one.'
    }
    if (error.status === 400) {
      return 'That is not a valid identifier. It should look like 01a093df-3607-707d-8342-f155ece35b0a.'
    }
  }
  return error instanceof Error ? error.message : 'The organization could not be checked.'
}

export function ChooseOrganization() {
  const auth = useAuth()
  const [value, setValue] = useState('')
  const [checking, setChecking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault()
    const candidate = value.trim()
    if (!candidate) return
    setChecking(true)
    setProblem(null)
    try {
      // Deliberately not through the configured client's organization getter: this identifier is
      // not committed to anything yet, and storing it before it is known to work is what produces
      // an app that is permanently broken until somebody clears their browser storage.
      const { error } = await api.GET('/api/v1/me', {
        headers: { 'X-Organization-Id': candidate },
      })
      if (error) throw error
      auth.setOrganizationId(candidate)
    } catch (cause) {
      setProblem(explain(cause))
    } finally {
      setChecking(false)
    }
  }

  return (
    <main>
      <h1>AI WorkOS</h1>
      <section aria-labelledby="choose-org-heading">
        <h2 id="choose-org-heading">Choose the organization you are working in</h2>
        <p>
          WorkOS never guesses this. Your organization identifier is shown when the organization is
          created; it is remembered in this browser once it has been checked.
        </p>
        <form onSubmit={(event) => void submit(event)}>
          <Field
            label="Organization identifier"
            hint="A UUID, for example 01a093df-3607-707d-8342-f155ece35b0a."
          >
            {(id) => (
              <input
                id={id}
                required
                value={value}
                spellCheck={false}
                autoComplete="off"
                onChange={(event) => setValue(event.target.value)}
                placeholder="01a093df-3607-707d-8342-f155ece35b0a"
              />
            )}
          </Field>
          {problem ? (
            <p role="alert" className="field__error">
              {problem}
            </p>
          ) : null}
          <button type="submit" disabled={checking || !value.trim()}>
            {checking ? 'Checking…' : 'Continue'}
          </button>
        </form>
        <p>
          <button type="button" onClick={auth.signOut}>
            Sign out
          </button>
        </p>
      </section>
    </main>
  )
}
