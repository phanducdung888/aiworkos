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
import type {
  ProposalDetail as ProposalDetailResource,
  ProposalStatus,
} from '@/api/hooks'
import {
  useApprovalFor,
  useCommitment,
  useDecideProposal,
  useEvent,
  useEvidence,
  useProposal,
  useProposals,
  useQueueApproval,
  useWorkItem,
} from '@/api/hooks'
import { Attachments } from '@/components/Attachments'
import { Empty, ErrorState, Loading } from '@/components/States'
import { Field } from '@/components/Field'
import { Badge } from '@/components/Badge'

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

/**
 * Which proposals a reader is looking at.
 *
 * Pending first, because that is the one that asks something of them. The decided states are
 * reachable on purpose: "what did the AI propose, and what did a person do about it" is a question
 * about history, and a screen that only ever showed the outstanding ones could not answer it.
 */
/** The same four states in the sentences that interpolate them, so a view name reads as Vietnamese
 *  prose rather than as an enum spliced into a sentence. */
const VIEW_NAMES: Record<ProposalStatus, string> = {
  pending: 'đang chờ',
  accepted: 'đã duyệt',
  accepted_with_edits: 'đã duyệt kèm chỉnh sửa',
  rejected: 'đã từ chối',
  expired: 'đã hết hạn',
  superseded: 'đã bị thay thế',
}

const VIEWS: { status: ProposalStatus; label: string }[] = [
  { status: 'pending', label: 'Đang chờ' },
  { status: 'accepted', label: 'Đã duyệt' },
  { status: 'rejected', label: 'Đã từ chối' },
  { status: 'expired', label: 'Đã hết hạn' },
]

export function ProposalList() {
  const [view, setView] = useState<ProposalStatus>('pending')
  const proposals = useProposals(view)

  if (proposals.isPending) return <Loading label="các đề xuất" />
  if (proposals.isError) {
    return <ErrorState error={proposals.error} retry={() => void proposals.refetch()} />
  }
  return (
    <section aria-labelledby="proposals-heading">
      <h1 id="proposals-heading">Đề xuất</h1>
      <p>
        {VIEWS.map((candidate) => (
          <button
            key={candidate.status}
            type="button"
            aria-pressed={view === candidate.status}
            onClick={() => setView(candidate.status)}
          >
            {candidate.label}
          </button>
        ))}
      </p>
      {proposals.data.length === 0 ? (
        <Empty>
          {view === 'pending'
            ? 'Không có đề xuất nào đang chờ quyết định.'
            : `Không có đề xuất nào ${VIEW_NAMES[view]}.`}
        </Empty>
      ) : (
      <table>
        <caption>
          {view === 'pending'
            ? 'Đang chờ quyết định. Chưa có gì ở đây được tạo ra.'
            : `Các đề xuất ${VIEW_NAMES[view]}.`}
        </caption>
        <thead>
          <tr>
            <th scope="col">Tóm tắt</th>
            <th scope="col">Tạo ra</th>
            <th scope="col">Độ tin cậy</th>
            <th scope="col">Hết hạn</th>
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
      )}
    </section>
  )
}

export function ProposalDetail() {
  const { proposalId = '' } = useParams()
  const proposal = useProposal(proposalId)

  if (proposal.isPending) return <Loading label="đề xuất" />
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
        <h2 id="action-heading">Điều sẽ xảy ra, chính xác</h2>
        {/* The action as approved, argument by argument. Not a sentence about it: this is what the
            hash covers and what the worker will run (ADR-0041). */}
        <dl data-testid="action">
          <dt>Công cụ</dt>
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
        <h2 id="evidence-heading">Căn cứ</h2>
        {evidence.isPending ? <Loading label="trích dẫn" /> : null}
        {evidence.isError ? <ErrorState error={evidence.error} /> : null}
        {evidence.data ? (
          <blockquote data-testid="excerpt" cite={proposal.source_event_id ?? undefined}>
            {evidence.data.excerpt}
          </blockquote>
        ) : null}
        {event.data ? (
          <>
            <p>
              From a {event.data.type} on {formatted(event.data.occurred_at)} ·{' '}
              <Link to={`/proposals?event=${event.data.id}`}>{event.data.source_system}</Link>
            </p>
            {/* The files that message carried. A reviewer deciding whether to accept a proposal
                extracted from an email is entitled to the invoice that came with it, before
                deciding rather than after. */}
            <Attachments eventId={event.data.id} attachments={event.data.attachments ?? []} />
          </>
        ) : null}
      </section>

      {pending && !decided ? (
        <section aria-labelledby="decide-heading">
          <h2 id="decide-heading">Quyết định</h2>
          <p>
            Duyệt tức là uỷ quyền hành động này trong 24 giờ. Nó không chạy ở đây — nó được xếp
            hàng và do hệ thống thực hiện.
          </p>
          <Field label="Lý do từ chối" hint="Chỉ bắt buộc khi từ chối.">
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
            Duyệt
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
            Từ chối
          </button>
          {decide.isError ? <ErrorState error={decide.error} /> : null}
        </section>
      ) : null}

      {queue.isError ? (
        <ErrorState error={queue.error} retry={() => queue.reset()} />
      ) : null}
      {/* Whenever it has been decided — not only in the session that decided it. Coming back to
          an approved proposal used to show a status and nothing else, which loses the answer to
          "what did a human approve, and what happened?" at exactly the moment somebody asks it. */}
      {decided || !pending ? <Outcome proposalId={proposal.id} /> : null}
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

  if (approval.isPending) return <Loading label="kết quả" />
  if (approval.isError) {
    // A rejected proposal has no ApprovalRecord, and that is the answer rather than a failure:
    // somebody looked and said no, and nothing was created.
    return (
      <section aria-labelledby="outcome-heading">
        <h2 id="outcome-heading">Kết quả</h2>
        <p data-testid="no-approval">
          Không có bản ghi phê duyệt nào. Không có gì được tạo ra từ đề xuất này.
        </p>
      </section>
    )
  }
  const record = approval.data

  return (
    <section aria-labelledby="outcome-heading">
      <h2 id="outcome-heading">Đã duyệt</h2>
      <dl>
        <dt>Quyết định lúc</dt>
        <dd>{formatted(record.decided_at)}</dd>
        <dt>Uỷ quyền đến</dt>
        <dd>{formatted(record.execution_expires_at)}</dd>
        <dt>Thực thi</dt>
        <dd data-testid="execution-status">
          <Badge kind="execution" value={record.execution_status} />
        </dd>
      </dl>
      {record.execution_error ? (
        <p role="alert">Hành động không được thực hiện: {record.execution_error}</p>
      ) : null}
      {record.execution_status === 'pending' && !approval.isFetching ? (
        <p>
          Vẫn đang chờ. Hành động đã được uỷ quyền và xếp hàng; chưa có gì chạy nó.
        </p>
      ) : null}
      <Created
        entityType={record.resulting_entity_type}
        entityId={record.resulting_entity_id}
      />
    </section>
  )
}

/**
 * What the execution actually produced, named and linked.
 *
 * Reads the type off the ApprovalRecord rather than off the Proposal: the Proposal says what was
 * *asked for*, and the approval says what was *done*. They agree today and the record is the one
 * that knows, so a tool that ever produces something other than it proposed is reported honestly
 * instead of mislabelled.
 */
function Created({
  entityType,
  entityId,
}: {
  entityType: string | null
  entityId: string | null
}) {
  const commitment = useCommitment(entityType === 'commitment' ? entityId : null)
  const work = useWorkItem(entityType === 'work' ? (entityId ?? '') : '')

  if (!entityId || !entityType) return null
  if (commitment.data) {
    return (
      <p>
        Đã tạo một lời hứa:{' '}
        <Link to={`/commitments/${commitment.data.id}`} data-testid="created-commitment">
          {commitment.data.statement}
        </Link>{' '}
        ({commitment.data.status})
      </p>
    )
  }
  if (work.data) {
    return (
      <p>
        Đã tạo một công việc:{' '}
        <Link to={`/work/${work.data.id}`} data-testid="created-work">
          {work.data.title}
        </Link>{' '}
        ({work.data.status})
      </p>
    )
  }
  // Executed, and this client does not have a surface for what it made. Say so rather than
  // rendering nothing, which reads as "the approval produced no result".
  return (
    <p data-testid="created-other">
      Đã tạo {entityType} ({entityId}).
    </p>
  )
}
