/**
 * Reviewing what the AI proposed, and deciding.
 *
 * This is the hinge of the whole autonomy model (PQ-3, rule 9). Everything before it is an
 * opinion; everything after it is authorised by the person who clicked. So the detail screen shows
 * the *exact* action the approval will be bound to — the tool, its version, every argument — and
 * the *verbatim* Evidence excerpt, never a summary of either. An approval screen that paraphrased
 * would break the property `action_hash` exists to guarantee: that what the approver read is what
 * the worker runs (ADR-0041).
 *
 * Approving executes nothing. The action is queued and a worker performs it (ADR-0044, ADR-0048),
 * which is why this polls the ApprovalRecord afterwards instead of reading a result out of the
 * response to the decision.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import type { ProposalDetail as ProposalDetailResource } from '@/api/hooks'
import {
  useApprovalFor,
  useCommitment,
  useDecideProposal,
  useEvent,
  useEvidence,
  useProposal,
  useProposals,
  useQueueApproval,
} from '@/api/hooks'
import { Empty, ErrorState, Loading } from '@/components/States'
import { Field } from '@/components/Field'

/**
 * The band, from the same floors the backend publishes (`BAND_FLOOR`, ADR-0050).
 *
 * Duplicated here rather than added to the API, because CP14 adds no field to the contract. It is
 * a display decision over a number the API already sends; if a third reader ever needs it, the
 * band belongs in the resource and not in a second copy of this function.
 */
export function band(confidence: number): string {
  if (confidence >= 85) return 'high'
  if (confidence >= 60) return 'medium'
  if (confidence > 0) return 'low'
  return 'unknown'
}

const formatted = (iso: string): string => new Date(iso).toLocaleString()

export function ProposalList() {
  const proposals = useProposals('pending')

  if (proposals.isPending) return <Loading label="proposals" />
  if (proposals.isError) {
    return <ErrorState error={proposals.error} retry={() => void proposals.refetch()} />
  }
  if (proposals.data.length === 0) {
    return (
      <section aria-labelledby="proposals-heading">
        <h1 id="proposals-heading">Proposals</h1>
        <Empty>Nothing is waiting for a decision.</Empty>
      </section>
    )
  }

  return (
    <section aria-labelledby="proposals-heading">
      <h1 id="proposals-heading">Proposals</h1>
      <table>
        <caption>Waiting for a decision. Nothing here has been created.</caption>
        <thead>
          <tr>
            <th scope="col">Summary</th>
            <th scope="col">Creates</th>
            <th scope="col">Confidence</th>
            <th scope="col">Expires</th>
          </tr>
        </thead>
        <tbody>
          {proposals.data.map((proposal) => (
            <tr key={proposal.id}>
              <td>
                <Link to={`/proposals/${proposal.id}`}>{proposal.summary}</Link>
              </td>
              <td>{proposal.target_type}</td>
              <td>
                {band(proposal.confidence)} ({proposal.confidence}%)
              </td>
              <td>{formatted(proposal.expires_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function ProposalDetail() {
  const { proposalId = '' } = useParams()
  const proposal = useProposal(proposalId)

  if (proposal.isPending) return <Loading label="the proposal" />
  if (proposal.isError) {
    return <ErrorState error={proposal.error} retry={() => void proposal.refetch()} />
  }
  return <Review proposal={proposal.data} />
}

function Review({ proposal }: { proposal: ProposalDetailResource }) {
  const [reason, setReason] = useState('')
  const [decided, setDecided] = useState(false)
  const decide = useDecideProposal()
  const queue = useQueueApproval()
  const navigate = useNavigate()
  const evidence = useEvidence(proposal.evidence_ids?.[0])
  const event = useEvent(proposal.source_event_id ?? '')
  const pending = proposal.status === 'pending'

  const action = proposal.action as {
    tool?: string
    tool_version?: string
    arguments?: Record<string, unknown>
  }
  const args = action.arguments ?? {}

  return (
    <section aria-labelledby="proposal-heading">
      <h1 id="proposal-heading">{proposal.summary}</h1>
      <p>
        Status {proposal.status} · creates a {proposal.target_type} · confidence{' '}
        <span data-testid="band">{band(proposal.confidence)}</span> ({proposal.confidence}%)
      </p>
      {proposal.reason ? <p>{proposal.reason}</p> : null}

      <section aria-labelledby="action-heading">
        <h2 id="action-heading">Exactly what will happen</h2>
        {/* The action as approved, argument by argument. Not a sentence about it: this is what the
            hash covers and what the worker will run (ADR-0041). */}
        <dl data-testid="action">
          <dt>Tool</dt>
          <dd>
            {action.tool} ({action.tool_version})
          </dd>
          {Object.entries(args).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
        <p className="field__hint">Action hash {proposal.action_hash}</p>
      </section>

      <section aria-labelledby="evidence-heading">
        <h2 id="evidence-heading">What it is based on</h2>
        {evidence.isPending ? <Loading label="the evidence" /> : null}
        {evidence.isError ? <ErrorState error={evidence.error} /> : null}
        {evidence.data ? (
          <blockquote data-testid="excerpt" cite={proposal.source_event_id ?? undefined}>
            {evidence.data.excerpt}
          </blockquote>
        ) : null}
        {event.data ? (
          <p>
            From a {event.data.type} on {formatted(event.data.occurred_at)} ·{' '}
            <Link to={`/proposals?event=${event.data.id}`}>{event.data.source_system}</Link>
          </p>
        ) : null}
      </section>

      {pending && !decided ? (
        <section aria-labelledby="decide-heading">
          <h2 id="decide-heading">Decide</h2>
          <p>
            Approving authorises this action for 24 hours. It does not run here — it is queued and
            performed by the system.
          </p>
          <Field label="Reason for rejecting" hint="Required only when rejecting.">
            {(id) => (
              <input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />
            )}
          </Field>
          <button
            type="button"
            disabled={decide.isPending}
            onClick={() =>
              decide.mutate(
                { proposal, decision: 'approved' },
                {
                  onSuccess: (record) => {
                    setDecided(true)
                    queue.mutate({ approvalId: record.id })
                  },
                },
              )
            }
          >
            Approve
          </button>
          <button
            type="button"
            disabled={decide.isPending || reason.trim() === ''}
            onClick={() =>
              decide.mutate(
                { proposal, decision: 'rejected', rejection_reason: reason },
                { onSuccess: () => navigate('/proposals') },
              )
            }
          >
            Reject
          </button>
          {decide.isError ? <ErrorState error={decide.error} /> : null}
        </section>
      ) : null}

      {queue.isError ? (
        <ErrorState error={queue.error} retry={() => queue.reset()} />
      ) : null}
      {decided ? <Outcome proposalId={proposal.id} /> : null}
    </section>
  )
}

/**
 * What the approval authorised, and what became of it.
 *
 * Polls until the execution reaches a terminal state, because the mutation happens in a worker
 * after this request ended. A failure is shown as a failure rather than as an absence — an empty
 * screen after an approval reads as "nothing happened", which is a different and worse claim.
 */
function Outcome({ proposalId }: { proposalId: string }) {
  const approval = useApprovalFor(proposalId, true)
  const commitment = useCommitment(
    approval.data?.resulting_entity_type === 'commitment'
      ? approval.data.resulting_entity_id
      : null,
  )

  if (approval.isPending) return <Loading label="the approval" />
  if (approval.isError) return <ErrorState error={approval.error} />
  const record = approval.data

  return (
    <section aria-labelledby="outcome-heading">
      <h2 id="outcome-heading">Approved</h2>
      <dl>
        <dt>Decided</dt>
        <dd>{formatted(record.decided_at)}</dd>
        <dt>Authorised until</dt>
        <dd>{formatted(record.execution_expires_at)}</dd>
        <dt>Execution</dt>
        <dd data-testid="execution-status">{record.execution_status}</dd>
      </dl>
      {record.execution_error ? (
        <p role="alert">The action was not performed: {record.execution_error}</p>
      ) : null}
      {record.execution_status === 'pending' && !approval.isFetching ? (
        <p>
          Still waiting. The action is authorised and queued; nothing has run it yet.
        </p>
      ) : null}
      {commitment.data ? (
        <p>
          Created a commitment:{' '}
          <Link to={`/commitments/${commitment.data.id}`} data-testid="created-commitment">
            {commitment.data.statement}
          </Link>{' '}
          ({commitment.data.status})
        </p>
      ) : null}
    </section>
  )
}
