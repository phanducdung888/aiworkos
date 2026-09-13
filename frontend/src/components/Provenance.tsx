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
import { ApiProblem } from '@/api/problem'
import { Attachments } from '@/components/Attachments'
import { Loading } from '@/components/States'
import { eventType, executionStatus } from '@/components/vocabulary'

const formatted = (iso: string): string => new Date(iso).toLocaleString()

export function nameOf(people: Person[] | undefined, personId: string | null): string {
  if (!personId) return 'Không ai'
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

  if (produced.isPending) return <Loading label="nguồn gốc" />
  if (produced.isError) return <Unavailable error={produced.error} />
  if (produced.data === null) {
    return (
      <section aria-labelledby="provenance-heading">
        <h2 id="provenance-heading">Nguồn gốc</h2>
        <p>{createdByHand ?? 'Do một người nhập trực tiếp. Không phải AI đề xuất.'}</p>
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

  if (proposal.isPending) return <Loading label="nguồn gốc" />
  if (proposal.isError) return <Unavailable error={proposal.error} />

  const action = proposal.data.action as {
    tool?: string
    tool_version?: string
    arguments?: Record<string, unknown>
  }

  return (
    <section aria-labelledby="provenance-heading">
      <h2 id="provenance-heading">Nguồn gốc</h2>

      <ol className="provenance">
        <li>
          <h3>Có người đã nói</h3>
          {evidence.data ? (
            // Verbatim, sliced from the Event itself. A paraphrase here would be Evidence the
            // service would have refused (BR-E-05), and showing one would undo the point of it.
            <blockquote data-testid="provenance-excerpt">{evidence.data.excerpt}</blockquote>
          ) : (
            <p>Đề xuất này không có trích dẫn nào kèm theo.</p>
          )}
          {event.data ? (
            <>
              <p>
                {eventType(event.data.type)} lúc {formatted(event.data.occurred_at)} qua{' '}
                {event.data.source_system}
              </p>
              {/* The files the message arrived with. Part of "where this came from" rather than a
                  surface of its own: a person questioning a Proposal wants the invoice that was
                  attached to the mail it was extracted from. */}
              <Attachments eventId={event.data.id} attachments={event.data.attachments ?? []} />
            </>
          ) : null}
        </li>

        <li>
          <h3>AI đã đọc</h3>
          {interaction.data ? (
            <p data-testid="provenance-interaction">
              {interaction.data.agent_identity} · {interaction.data.provider}/
              {interaction.data.model} ({interaction.data.model_version}) · prompt{' '}
              {interaction.data.prompt_id}@{interaction.data.prompt_version} ·{' '}
              {interaction.data.status}
            </p>
          ) : (
            <p>Không có lần chạy AI nào được ghi lại cho trích dẫn này.</p>
          )}
        </li>

        <li>
          <h3>AI đề xuất đúng điều này</h3>
          {/* The action as approved, argument by argument — what the hash covers and what the
              worker ran (ADR-0041). */}
          <dl data-testid="provenance-action">
            <dt>Công cụ</dt>
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
            <Link to={`/proposals/${proposalId}`}>Đề xuất</Link> · mã băm{' '}
            {proposal.data.action_hash} · độ tin cậy {proposal.data.confidence}%
          </p>
        </li>

        <li>
          <h3>Một người đã duyệt</h3>
          {approval.data ? (
            <p data-testid="provenance-approval">
              {nameOf(people, approval.data.approver_person_id)} lúc{' '}
              {formatted(approval.data.decided_at)} · thực thi{' '}
              {executionStatus(approval.data.execution_status)}
              {approval.data.execution_error ? ` (${approval.data.execution_error})` : ''}
            </p>
          ) : (
            <p>Chưa có quyết định. Chưa có gì được tạo ra từ đề xuất này.</p>
          )}
        </li>
      </ol>
    </section>
  )
}
/**
 * Provenance that could not be read, said quietly.
 *
 * Deliberately not an `alert` and deliberately not `ErrorState`. This panel explains an entity that
 * is on the screen and fine; a failure to load the explanation is a gap in what we can tell the
 * reader, not a failure of the thing they are looking at. Shouting it would compete with the page's
 * own errors and would imply the record itself is broken.
 *
 * Permission is still named, because "you may not see this" and "we could not look" are different
 * facts and only one of them is worth asking somebody about.
 */
function Unavailable({ error }: { error: unknown }) {
  const forbidden = error instanceof ApiProblem && error.isForbidden
  return (
    <section aria-labelledby="provenance-heading">
      <h2 id="provenance-heading">Nguồn gốc</h2>
      <p data-testid="provenance-unavailable">
        {forbidden
          ? 'Bạn không có quyền xem nguồn gốc của mục này.'
          : 'Không tải được nguồn gốc của mục này.'}
      </p>
    </section>
  )
}
