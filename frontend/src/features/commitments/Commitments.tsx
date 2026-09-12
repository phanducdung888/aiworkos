/**
 * Promises: the list, and one of them in full.
 *
 * A Commitment is what somebody said they would do, recorded as they said it. It is not an
 * assignment and the UI must not read like one: there is no "assign", no reassignment, and the
 * lifecycle belongs to the people the promise is about (BR-C-09) rather than to whoever happens to
 * be looking. The API refuses what a reader may not do; this offers the transitions the domain
 * allows from where the promise currently is (BR-C-04) and lets the refusal explain itself.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { Commitment, CommitmentStatus } from '@/api/hooks'
import {
  useChangeCommitmentStatus,
  useCommitment,
  useCommitments,
  useOverdueCommitments,
  usePeople,
} from '@/api/hooks'
import { Empty, ErrorState, Loading } from '@/components/States'
import { Provenance, nameOf } from '@/components/Provenance'

/**
 * BR-C-04's table, as the UI needs it.
 *
 * Duplicated from the domain rather than fetched, because it is a *shape* of the screen — which
 * buttons exist — and not a decision. The decision is still the server's: every transition below is
 * re-checked there against the same table and against BR-C-09's question of who may make it, so a
 * button this list gets wrong produces a refusal rather than a wrong write.
 */
const TRANSITIONS: Record<string, CommitmentStatus[]> = {
  captured: ['open', 'disputed', 'withdrawn'],
  open: ['fulfilled', 'missed', 'renegotiated', 'cancelled'],
  renegotiated: ['open'],
  missed: ['fulfilled'],
  disputed: [],
  withdrawn: [],
  fulfilled: [],
  cancelled: [],
}

const LABELS: Record<CommitmentStatus, string> = {
  captured: 'Captured',
  open: 'Acknowledge',
  fulfilled: 'Mark fulfilled',
  missed: 'Mark missed',
  renegotiated: 'Renegotiate',
  cancelled: 'Cancel',
  disputed: 'Dispute',
  withdrawn: 'Withdraw',
}

export function due(commitment: Commitment): string {
  // A promise with no date is a complete record, and `vague` is the honest reason it has none: the
  // message named a deadline this system would not guess at (ADR-0055).
  if (commitment.due_date === null) return `No date (${commitment.due_precision})`
  if (commitment.due_precision === 'exact') return commitment.due_date
  return `${commitment.due_date} (${commitment.due_precision})`
}

export function CommitmentList() {
  const [overdueOnly, setOverdueOnly] = useState(false)
  const all = useCommitments()
  const overdue = useOverdueCommitments()
  const people = usePeople()
  const shown = overdueOnly ? overdue : all

  if (shown.isPending) return <Loading label="commitments" />
  if (shown.isError) return <ErrorState error={shown.error} retry={() => void shown.refetch()} />

  return (
    <section aria-labelledby="commitments-heading">
      <h1 id="commitments-heading">Commitments</h1>
      <p>
        <label>
          <input
            type="checkbox"
            checked={overdueOnly}
            onChange={(changed) => setOverdueOnly(changed.target.checked)}
          />{' '}
          Overdue only
        </label>{' '}
        {/* BR-C-06: asking is not deciding. Nothing is written by looking at this. */}
        <span className="field__hint">
          Overdue is computed on read. Promises with no date are not listed — a deadline nobody set
          cannot be missed.
        </span>
      </p>

      {shown.data.length === 0 ? (
        <Empty>{overdueOnly ? 'Nothing is overdue.' : 'No commitments have been recorded.'}</Empty>
      ) : (
        <table>
          <caption>What people have said they would do.</caption>
          <thead>
            <tr>
              <th scope="col">Promise</th>
              <th scope="col">Committer</th>
              <th scope="col">Status</th>
              <th scope="col">Due</th>
            </tr>
          </thead>
          <tbody>
            {shown.data.map((commitment) => (
              <tr key={commitment.id}>
                <td>
                  <Link to={`/commitments/${commitment.id}`}>{commitment.statement}</Link>
                </td>
                <td>{nameOf(people.data, commitment.committed_by_person_id)}</td>
                <td>{commitment.status}</td>
                <td>{due(commitment)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export function CommitmentDetail() {
  const { commitmentId = '' } = useParams()
  const commitment = useCommitment(commitmentId)
  const people = usePeople()
  const change = useChangeCommitmentStatus()

  if (commitment.isPending) return <Loading label="the commitment" />
  if (commitment.isError) {
    return <ErrorState error={commitment.error} retry={() => void commitment.refetch()} />
  }

  const promise = commitment.data
  const available = TRANSITIONS[promise.status] ?? []

  return (
    <section aria-labelledby="commitment-heading">
      <h1 id="commitment-heading">{promise.statement}</h1>
      <dl>
        <dt>Committer</dt>
        <dd data-testid="committer">{nameOf(people.data, promise.committed_by_person_id)}</dd>
        <dt>Status</dt>
        <dd data-testid="status">{promise.status}</dd>
        <dt>Due</dt>
        <dd data-testid="due">{due(promise)}</dd>
        <dt>Confidence</dt>
        <dd>{promise.confidence}%</dd>
      </dl>

      {available.length > 0 ? (
        <section aria-labelledby="lifecycle-heading">
          <h2 id="lifecycle-heading">Move it on</h2>
          <p className="field__hint">
            Only the committer, the recipient, or a lead in their chain may do this (BR-C-09). If
            that is not you, the change will be refused.
          </p>
          {available.map((target) => (
            <button
              key={target}
              type="button"
              disabled={change.isPending}
              onClick={() => change.mutate({ commitment: promise, target })}
            >
              {LABELS[target]}
            </button>
          ))}
          {change.isError ? <ErrorState error={change.error} /> : null}
        </section>
      ) : (
        <p data-testid="terminal">
          This promise is {promise.status}. There is nowhere further for it to go.
        </p>
      )}

      <Provenance
        entityId={promise.id}
        people={people.data}
        createdByHand="Recorded directly by a person. No AI proposed it."
      />
    </section>
  )
}
