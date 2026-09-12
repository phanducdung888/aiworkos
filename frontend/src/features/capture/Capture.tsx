/**
 * Capturing a message and asking the system what it found in it.
 *
 * Two acts, deliberately separate and in this order. Capturing records that a message exists;
 * analysing is a request for an opinion about it. Merging them into one button would make every
 * capture spend a model call and would put "the AI ran" on a path a person did not choose
 * (PQ-3, BR-AI-01).
 *
 * The participant list is the part worth reading. A channel message arrives with a handle and no
 * Person, and whether that handle resolves decides whether a promise in the text can be attributed
 * to anybody (ADR-0054). So the resolution is shown rather than hidden: after capture, each
 * participant says who it resolved to or that it resolved to nobody, and the reader can see why
 * the analysis proposed a commitment or only a piece of work.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { Analysis, EventDetail, ParticipantRole } from '@/api/hooks'
import { useAgentPolicy, useAnalyzeEvent, useCaptureEvent, usePeople } from '@/api/hooks'
import { ErrorState } from '@/components/States'
import { Field } from '@/components/Field'

const ROLES: ParticipantRole[] = ['speaker', 'organiser', 'recipient', 'mentioned']

interface Draft {
  role: ParticipantRole
  /** A person the capturer can name, or '' for somebody only the channel knows. */
  personId: string
  handle: string
}

const emptyDraft = (): Draft => ({ role: 'speaker', personId: '', handle: '' })

export function Capture() {
  const [body, setBody] = useState('')
  const [drafts, setDrafts] = useState<Draft[]>([emptyDraft()])
  const [event, setEvent] = useState<EventDetail | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const people = usePeople()
  const policy = useAgentPolicy()
  const capture = useCaptureEvent()
  const analyze = useAnalyzeEvent()

  const update = (index: number, change: Partial<Draft>) =>
    setDrafts((current) =>
      current.map((draft, at) => (at === index ? { ...draft, ...change } : draft)),
    )

  const named = (personId: string | null) =>
    people.data?.find((person) => person.id === personId)?.display_name ?? personId

  return (
    <section aria-labelledby="capture-heading">
      <h1 id="capture-heading">Capture a message</h1>

      <form
        onSubmit={(submitted) => {
          submitted.preventDefault()
          setAnalysis(null)
          capture.mutate(
            {
              type: 'EXTERNAL_MESSAGE',
              occurred_at: new Date().toISOString(),
              body_text: body,
              source_system: 'openclaw.whatsapp',
              participants: drafts
                .filter((draft) => draft.personId !== '' || draft.handle.trim() !== '')
                .map((draft) => ({
                  role: draft.role,
                  person_id: draft.personId === '' ? null : draft.personId,
                  external_handle: draft.handle.trim() === '' ? null : draft.handle.trim(),
                })),
            },
            { onSuccess: setEvent },
          )
        }}
      >
        <Field label="Message" hint="Paste what was said. It is stored verbatim and cited verbatim.">
          {(id) => (
            <textarea
              id={id}
              required
              rows={5}
              value={body}
              onChange={(changed) => setBody(changed.target.value)}
              placeholder="I will send the revised quote on Friday."
            />
          )}
        </Field>

        <fieldset>
          <legend>Participants</legend>
          {drafts.map((draft, index) => (
            <div key={index} className="participant">
              <Field label={`Role ${index + 1}`}>
                {(id) => (
                  <select
                    id={id}
                    value={draft.role}
                    onChange={(changed) =>
                      update(index, { role: changed.target.value as ParticipantRole })
                    }
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <Field
                label={`Person ${index + 1}`}
                hint="Leave unset for somebody only the channel knows."
              >
                {(id) => (
                  <select
                    id={id}
                    value={draft.personId}
                    onChange={(changed) => update(index, { personId: changed.target.value })}
                  >
                    <option value="">Not named</option>
                    {(people.data ?? []).map((person) => (
                      <option key={person.id} value={person.id}>
                        {person.display_name}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <Field label={`Handle ${index + 1}`} hint="The channel identifier, e.g. a number.">
                {(id) => (
                  <input
                    id={id}
                    value={draft.handle}
                    onChange={(changed) => update(index, { handle: changed.target.value })}
                    placeholder="+84900000001"
                  />
                )}
              </Field>
            </div>
          ))}
          <button type="button" onClick={() => setDrafts((current) => [...current, emptyDraft()])}>
            Add participant
          </button>
        </fieldset>

        <button type="submit" disabled={capture.isPending || body.trim() === ''}>
          {capture.isPending ? 'Capturing…' : 'Capture'}
        </button>
      </form>

      {capture.isError ? <ErrorState error={capture.error} /> : null}

      {event ? (
        <section aria-labelledby="captured-heading">
          <h2 id="captured-heading">Captured</h2>
          <table>
            <caption>
              Who this message resolved to. An unresolved handle is kept, and no promise is
              attributed to anybody (BR-I-06).
            </caption>
            <thead>
              <tr>
                <th scope="col">Role</th>
                <th scope="col">Handle</th>
                <th scope="col">Resolved to</th>
              </tr>
            </thead>
            <tbody>
              {(event.participants ?? []).map((participant) => (
                <tr key={participant.id}>
                  <td>{participant.role}</td>
                  <td>{participant.external_handle ?? '—'}</td>
                  <td data-testid="resolution">
                    {participant.person_id ? (
                      <>
                        {named(participant.person_id)} ({participant.match_confidence}%)
                      </>
                    ) : (
                      'Nobody'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <button
            type="button"
            disabled={analyze.isPending}
            onClick={() => analyze.mutate({ eventId: event.id }, { onSuccess: setAnalysis })}
          >
            {analyze.isPending ? 'Analysing…' : 'Analyse this message'}
          </button>
          {analyze.isError ? <ErrorState error={analyze.error} /> : null}
        </section>
      ) : null}

      {analysis ? (
        <section aria-labelledby="analysis-heading">
          <h2 id="analysis-heading">What the analysis proposed</h2>
          {/* Nothing was written. Whatever is listed here is waiting for a person (PQ-3). */}
          <p>
            Nothing has been created. {analysis.proposal_ids?.length ?? 0} proposal(s) are waiting
            for a decision, and {analysis.low_confidence ?? 0} finding(s) were too uncertain to
            raise.
          </p>
          <ul>
            {(analysis.proposal_ids ?? []).map((proposalId) => (
              <li key={proposalId}>
                <Link to={`/proposals/${proposalId}`}>Review proposal</Link>
              </li>
            ))}
          </ul>
          {(analysis.proposal_ids?.length ?? 0) === 0 ? (
            // Absence is denial (ADR-0047), and "the AI is switched off" looks exactly like "the
            // AI found nothing" unless somebody says which it was.
            policy.data?.length === 0 ? (
              <p data-testid="policy-empty">
                Nothing was proposed, and this organization has not granted its agents any
                capability yet. Until an administrator does, every proposal is denied — the model
                was not the thing that declined.
              </p>
            ) : (
              <p>
                Nothing was proposed. An unresolved speaker or a low-confidence reading both end
                here, which is the system declining to guess rather than finding nothing.
              </p>
            )
          ) : null}
        </section>
      ) : null}
    </section>
  )
}
