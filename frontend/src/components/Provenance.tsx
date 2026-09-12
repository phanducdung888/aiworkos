/**
 * "Why does WorkOS believe this?" — answered for any entity the AI path produced.
 *
 * ADR-0056: the chain is walked rather than copied onto the entity, so this component does the
 * walking. Entity → Proposal (the exact action, and its hash) → ApprovalRecord (who authorised it,
 * and what the execution did) → Evidence (the verbatim words) → Event (the message) and the
 * AIInteraction that read it.
 *
 * Two things it will not do. It never paraphrases the Evidence — the excerpt is the quote or it is
 * nothing, because a summary of a citation is not a citation (BR-E-05). And it never implies a
 * chain that is not there: an entity a person created by hand says so, rather than rendering an
 * empty provenance panel that reads as missing data.
 *
 * Shared by Commitment and Work on purpose. The chain has the same shape whatever sits at the end
 * of it, and two copies of this would be two places for the explanation to drift.
 */
import { Link } from 'react-router-dom'
import type { Person } from '@/api/hooks'
import {
  useAiInteraction,
  useApprovalFor,
  useEvent,
  useEvidence,
  useProducedBy,
  useProposal,
} from '@/api/hooks'
import { ErrorState, Loading } from '@/components/States'

const formatted = (iso: string): string => new Date(iso).toLocaleString()

export function nameOf(people: Person[] | undefined, personId: string | null): string {
  if (!personId) return 'Nobody'
  return people?.find((person) => person.id === personId)?.display_name ?? personId
}

export function Provenance({
  entityId,
  people,
  createdByHand,
}: {
  entityId: string
  people?: Person[]
  /** What to say when nothing in the AI path produced this. */
  createdByHand?: string
}) {
  const produced = useProducedBy(entityId)

  if (produced.isPending) return <Loading label="where this came from" />
  if (produced.isError) return <ErrorState error={produced.error} />
  if (produced.data === null) {
    return (
      <section aria-labelledby="provenance-heading">
        <h2 id="provenance-heading">Where this came from</h2>
        <p>{createdByHand ?? 'Entered directly by a person. No AI proposed it.'}</p>
      </section>
    )
  }
  return <Chain proposalId={produced.data.id} people={people} />
}

function Chain({ proposalId, people }: { proposalId: string; people?: Person[] }) {
  const proposal = useProposal(proposalId)
  const approval = useApprovalFor(proposalId, true, { poll: false })
  const evidence = useEvidence(proposal.data?.evidence_ids?.[0])
  const event = useEvent(proposal.data?.source_event_id ?? '')
  const interaction = useAiInteraction(evidence.data?.produced_by_id)

  if (proposal.isPending) return <Loading label="where this came from" />
  if (proposal.isError) return <ErrorState error={proposal.error} />

  const action = proposal.data.action as {
    tool?: string
    tool_version?: string
    arguments?: Record<string, unknown>
  }

  return (
    <section aria-labelledby="provenance-heading">
      <h2 id="provenance-heading">Where this came from</h2>

      <ol className="provenance">
        <li>
          <h3>Somebody said it</h3>
          {evidence.data ? (
            // Verbatim, sliced from the Event itself. A paraphrase here would be Evidence the
            // service would have refused (BR-E-05), and showing one would undo the point of it.
            <blockquote data-testid="provenance-excerpt">{evidence.data.excerpt}</blockquote>
          ) : (
            <p>No citation is attached to this proposal.</p>
          )}
          {event.data ? (
            <p>
              {event.data.type} on {formatted(event.data.occurred_at)} via{' '}
              {event.data.source_system}
            </p>
          ) : null}
        </li>

        <li>
          <h3>The AI read it</h3>
          {interaction.data ? (
            <p data-testid="provenance-interaction">
              {interaction.data.agent_identity} · {interaction.data.provider}/
              {interaction.data.model} ({interaction.data.model_version}) · prompt{' '}
              {interaction.data.prompt_id}@{interaction.data.prompt_version} ·{' '}
              {interaction.data.status}
            </p>
          ) : (
            <p>No AI interaction is recorded against this citation.</p>
          )}
        </li>

        <li>
          <h3>It proposed exactly this</h3>
          {/* The action as approved, argument by argument — what the hash covers and what the
              worker ran (ADR-0041). */}
          <dl data-testid="provenance-action">
            <dt>Tool</dt>
            <dd>
              {action.tool} ({action.tool_version})
            </dd>
            {Object.entries(action.arguments ?? {}).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
              </div>
            ))}
          </dl>
          <p className="field__hint">
            <Link to={`/proposals/${proposalId}`}>Proposal</Link> · hash{' '}
            {proposal.data.action_hash} · confidence {proposal.data.confidence}%
          </p>
        </li>

        <li>
          <h3>A person approved it</h3>
          {approval.data ? (
            <p data-testid="provenance-approval">
              {nameOf(people, approval.data.approver_person_id)} on{' '}
              {formatted(approval.data.decided_at)} · execution{' '}
              {approval.data.execution_status}
              {approval.data.execution_error ? ` (${approval.data.execution_error})` : ''}
            </p>
          ) : (
            <p>Not yet decided. Nothing has been created from this proposal.</p>
          )}
        </li>
      </ol>
    </section>
  )
}
