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
  if (queries.some((query) => query.isPending)) return <Loading label="what needs attention" />
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
      <h1 id="attention-heading">Attention</h1>
      {total === 0 ? (
        <p data-testid="all-clear">
          Nothing needs attention. No promise is overdue, no proposal is waiting, and no work is
          blocked.
        </p>
      ) : null}

      <Section
        title="Overdue promises"
        why="Past their date and still open. Promises with no date are not counted — a deadline nobody set cannot be missed (BR-C-06)."
        items={overdueCommitments.data ?? []}
        testId="overdue-commitments"
        render={(promise) => <CommitmentRow promise={promise} people={people.data} />}
      />

      <Section
        title={`Promises due within ${SOON_DAYS} days`}
        why="Open, dated, and close. Nothing has gone wrong yet."
        items={dueSoon.data ?? []}
        testId="due-soon"
        render={(promise) => <CommitmentRow promise={promise} people={people.data} />}
      />

      <Section
        title="Promises nobody has confirmed"
        why="Captured from a message and never acknowledged by the person it names. Until somebody does, this is what the system heard, not what they agreed (BR-C-05)."
        items={unacknowledged.data ?? []}
        testId="unacknowledged"
        render={(promise) => <CommitmentRow promise={promise} people={people.data} />}
      />

      <Section
        title="Waiting for a decision"
        why="The AI proposed these and nothing has been created. Approving is the only way they become real."
        items={pending.data ?? []}
        testId="pending-proposals"
        render={(proposal) => (
          <>
            <Link to={`/proposals/${proposal.id}`}>{proposal.summary}</Link>{' '}
            <span className="field__hint">
              creates a {proposal.target_type}
              {isExpiringSoon(proposal) ? ' · expires soon' : ''}
            </span>
          </>
        )}
        note={
          expiring.length > 0
            ? `${expiring.length} of these expire within ${EXPIRING_HOURS} hours.`
            : undefined
        }
      />

      <Section
        title="Blocked work"
        why="Stopped, with a recorded cause. Blocked work always has one (BR-W-04)."
        items={blocked.data ?? []}
        testId="blocked-work"
        render={(item) => (
          <>
            <Link to={`/work/${item.id}`}>{item.title}</Link>{' '}
            <span className="field__hint">{item.blocked_reason ?? 'no reason recorded'}</span>
          </>
        )}
      />

      <Section
        title="Overdue work"
        why="Past its date and not finished."
        items={overdueWork}
        testId="overdue-work"
        render={(item) => (
          <>
            <Link to={`/work/${item.id}`}>{item.title}</Link>{' '}
            <span className="field__hint">
              due {item.due_date} · {item.status}
            </span>
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
        {nameOf(people, promise.committed_by_person_id)} · {due(promise)} · {promise.status}
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
    <section aria-labelledby={`${testId}-heading`} data-testid={testId}>
      <h2 id={`${testId}-heading`}>
        {title} ({items.length})
      </h2>
      <p className="field__hint">{why}</p>
      {note ? <p data-testid={`${testId}-note`}>{note}</p> : null}
      {items.length === 0 ? (
        <p>None.</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item.id}>{render(item)}</li>
          ))}
        </ul>
      )}
    </section>
  )
}
