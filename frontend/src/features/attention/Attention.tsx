/**
 * What needs a person today.
 *
 * Every number on this screen is a fact the database already knows, computed by a rule somebody
 * can read. Nothing here is scored, ranked or summarised by a model, and that is the design:
 * detection is deterministic and explainable, interpretation is the AI's job somewhere else
 * (rule 5). A dashboard that showed an AI-generated "risk" would be asking to be trusted about
 * something nobody could check.
 *
 * Each section is a predicate, not a recency window. "Overdue", "blocked", "waiting for a
 * decision" stay true however the list is ordered or paged; "the ten most recent things" would
 * silently become the ten oldest of a page, which is a different and wrong claim.
 *
 * Nothing on this page writes. Asking whether a promise is overdue must never be what makes it
 * missed — that is a transition with an actor and an audit entry (BR-C-06).
 */
import { Link } from 'react-router-dom'
import type { Commitment, Person, Proposal, Work } from '@/api/hooks'
import {
  useCommitments,
  useOverdueCommitments,
  usePeople,
  useProposals,
  useWork,
} from '@/api/hooks'
import { ErrorState, Loading } from '@/components/States'
import { nameOf } from '@/components/Provenance'
import { Badge } from '@/components/Badge'
import { targetType } from '@/components/vocabulary'
import { due } from '@/features/commitments/Commitments'

/** How far ahead "soon" looks. A working week, and stated rather than tuned in silence. */
export const SOON_DAYS = 7

/** How close to its deadline a pending Proposal is worth pointing at. */
export const EXPIRING_HOURS = 6

export function inDays(days: number, from: Date = new Date()): string {
  const when = new Date(from)
  when.setDate(when.getDate() + days)
  return when.toISOString().slice(0, 10)
}

/** Statuses that mean the work is finished with, one way or another (BR-W-03). */
const SETTLED = new Set(['done', 'cancelled', 'rejected'])

export function isExpiringSoon(proposal: Proposal, now: Date = new Date()): boolean {
  const hours = (new Date(proposal.expires_at).getTime() - now.getTime()) / 3_600_000
  return hours <= EXPIRING_HOURS
}

export function Attention() {
  const people = usePeople()
  const overdueCommitments = useOverdueCommitments()
  const dueSoon = useCommitments({ status: 'open', due_before: inDays(SOON_DAYS) })
  const unacknowledged = useCommitments({ status: 'captured' })
  const pending = useProposals('pending')
  const blocked = useWork({ status: 'blocked' })
  const dated = useWork({ due_before: inDays(0) })

  const queries = [overdueCommitments, dueSoon, unacknowledged, pending, blocked, dated]
  if (queries.some((query) => query.isPending)) return <Loading label="những việc cần chú ý" />
  const failed = queries.find((query) => query.isError)
  if (failed) return <ErrorState error={failed.error} retry={() => void failed.refetch()} />

  // `due_before` cannot also exclude finished work in one call, so the settled items are dropped
  // here. Deterministic either way; the narrowing is stated so nobody reads this as a filter the
  // API applied.
  const overdueWork = (dated.data ?? []).filter((item) => !SETTLED.has(item.status))
  const expiring = (pending.data ?? []).filter((proposal) => isExpiringSoon(proposal))

  const total =
    (overdueCommitments.data?.length ?? 0) +
    (dueSoon.data?.length ?? 0) +
    (unacknowledged.data?.length ?? 0) +
    (pending.data?.length ?? 0) +
    (blocked.data?.length ?? 0) +
    overdueWork.length

  return (
    <section aria-labelledby="attention-heading">
      <h1 id="attention-heading">Cần chú ý</h1>
      {total === 0 ? (
        <p data-testid="all-clear">
          Không có gì cần chú ý. Không lời hứa nào quá hạn, không đề xuất nào đang chờ, và không
          công việc nào bị chặn.
        </p>
      ) : null}

      <Section
        title="Lời hứa quá hạn"
        why="Đã qua hạn mà vẫn còn mở. Lời hứa không có hạn thì không được tính — một hạn chót không ai đặt ra thì không thể bị lỡ (BR-C-06)."
        items={overdueCommitments.data ?? []}
        testId="overdue-commitments"
        render={(promise) => <CommitmentRow promise={promise} people={people.data} />}
      />

      <Section
        title={`Lời hứa đến hạn trong ${SOON_DAYS} ngày tới`}
        why="Đang mở, có hạn, và sắp đến. Chưa có gì sai cả."
        items={dueSoon.data ?? []}
        testId="due-soon"
        render={(promise) => <CommitmentRow promise={promise} people={people.data} />}
      />

      <Section
        title="Lời hứa chưa ai xác nhận"
        why="Ghi nhận từ một tin nhắn và người được nêu tên chưa xác nhận. Chừng nào chưa ai xác nhận, đây là điều hệ thống nghe được, không phải điều họ đồng ý (BR-C-05)."
        items={unacknowledged.data ?? []}
        testId="unacknowledged"
        render={(promise) => <CommitmentRow promise={promise} people={people.data} />}
      />

      <Section
        title="Đang chờ quyết định"
        why="AI đã đề xuất và chưa có gì được tạo ra. Chỉ khi bạn duyệt thì chúng mới thành thật."
        items={pending.data ?? []}
        testId="pending-proposals"
        render={(proposal) => (
          <>
            <Link to={`/proposals/${proposal.id}`}>{proposal.summary}</Link>{' '}
            <span className="field__hint">
              tạo ra {targetType(proposal.target_type)}
              {isExpiringSoon(proposal) ? ' · sắp hết hạn' : ''}
            </span>
          </>
        )}
        note={
          expiring.length > 0
            ? `${expiring.length} trong số này sẽ hết hạn trong ${EXPIRING_HOURS} giờ tới.`
            : undefined
        }
      />

      <Section
        title="Công việc bị chặn"
        why="Đã dừng, và có lý do được ghi lại. Công việc bị chặn luôn có lý do (BR-W-04)."
        items={blocked.data ?? []}
        testId="blocked-work"
        render={(item) => (
          <>
            <Link to={`/work/${item.id}`}>{item.title}</Link>{' '}
            <span className="field__hint">{item.blocked_reason ?? 'không ghi lý do'}</span>
          </>
        )}
      />

      <Section
        title="Công việc quá hạn"
        why="Đã qua hạn mà chưa xong."
        items={overdueWork}
        testId="overdue-work"
        render={(item) => (
          <>
            <Link to={`/work/${item.id}`}>{item.title}</Link>{' '}
            <span className="field__hint">hạn {item.due_date}</span>{' '}
            <Badge kind="work" value={item.status} />
          </>
        )}
      />
    </section>
  )
}

function CommitmentRow({ promise, people }: { promise: Commitment; people?: Person[] }) {
  return (
    <>
      <Link to={`/commitments/${promise.id}`}>{promise.statement}</Link>{' '}
      <span className="field__hint">
        {nameOf(people, promise.committed_by_person_id)} · {due(promise)}{' '}
        <Badge kind="commitment" value={promise.status} />
      </span>
    </>
  )
}

/**
 * One section, and the rule that produced it.
 *
 * `why` is not decoration. A number a reader cannot account for is a number they will either
 * over-trust or ignore, and every one of these comes from a rule that can be stated in a sentence.
 * Empty sections are rendered rather than hidden, because "no promises are overdue" is the answer
 * somebody came here for.
 */
function Section<T extends Commitment | Proposal | Work>({
  title,
  why,
  items,
  testId,
  render,
  note,
}: {
  title: string
  why: string
  items: T[]
  testId: string
  render: (item: T) => React.ReactNode
  note?: string
}) {
  return (
    // Mục rỗng vẫn hiện, nhưng thu lại thành một dòng. "Không lời hứa nào quá hạn" là câu trả lời
    // người ta đến đây để nghe — giấu nó đi thì màn hình im lặng, còn để nó chiếm cả một tấm thẻ
    // thì sáu mục mà bốn rỗng sẽ đẩy phần thật sự cần đọc xuống dưới màn hình.
    <section
      aria-labelledby={`${testId}-heading`}
      data-testid={testId}
      className={items.length === 0 ? 'panel panel--settled' : 'panel'}
    >
      <h2 id={`${testId}-heading`}>
        {title} <span className="panel__count">{items.length}</span>
      </h2>
      <p className="field__hint">{why}</p>
      {note ? (
        <p className="panel__note" data-testid={`${testId}-note`}>
          {note}
        </p>
      ) : null}
      {items.length === 0 ? (
        <p className="panel__settled">Không có.</p>
      ) : (
        <ul className="rows">
          {items.map((item) => (
            <li key={item.id}>{render(item)}</li>
          ))}
        </ul>
      )}
    </section>
  )
}
