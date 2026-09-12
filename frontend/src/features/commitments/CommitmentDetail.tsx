/**
 * One commitment, read-only.
 *
 * Exists so that "the AI created this" ends somewhere a person can look at, rather than at an id
 * in a success message. Read-only on purpose: CP14 makes the loop reachable and does not add a way
 * to edit a promise, which the API already has and the UI can grow when somebody needs it.
 */
import { Link, useParams } from 'react-router-dom'
import { useCommitment } from '@/api/hooks'
import { ErrorState, Loading } from '@/components/States'

export function CommitmentDetail() {
  const { commitmentId = '' } = useParams()
  const commitment = useCommitment(commitmentId)

  if (commitment.isPending) return <Loading label="the commitment" />
  if (commitment.isError) {
    return <ErrorState error={commitment.error} retry={() => void commitment.refetch()} />
  }

  const promise = commitment.data
  return (
    <section aria-labelledby="commitment-heading">
      <h1 id="commitment-heading">{promise.statement}</h1>
      <dl>
        <dt>Status</dt>
        <dd>{promise.status}</dd>
        <dt>Due</dt>
        {/* A promise with no date is a complete record. Conversational dates are rarely exact, and
            the precision is part of the fact rather than a caveat on it (BR-C-05). */}
        <dd>{promise.due_date ?? `Not stated (${promise.due_precision})`}</dd>
        <dt>Captured from</dt>
        <dd>
          {promise.origin_event_id ? (
            <Link to={`/proposals?event=${promise.origin_event_id}`}>the source message</Link>
          ) : (
            'Entered directly'
          )}
        </dd>
      </dl>
    </section>
  )
}
