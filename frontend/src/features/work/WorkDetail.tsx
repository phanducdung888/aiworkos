/**
 * One work item: its state, who is on it, and what is holding it up.
 *
 * Three rules are visible here rather than described:
 *
 * - only the transitions the state machine allows are offered (BR-W-03), so an impossible move is
 *   not a button that returns 422;
 * - ownership changes in one call (`PUT owner`), never end-then-assign, because that pair is the
 *   route around REASSIGN the authorization model refuses (ADR-0032);
 * - a stale edit is reported as somebody else's change, not as a generic failure (BR-G-06).
 */
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  useAssignments,
  useDependencies,
  useEndAssignment,
  useSetOwner,
  useAssign,
  useChangeWorkStatus,
  useWorkItem,
} from '@/api/hooks'
import type { Work, WorkStatus } from '@/api/hooks'
import { Empty, ErrorState, Loading } from '@/components/States'
import { PersonPicker } from '@/components/PersonPicker'
import { Provenance } from '@/components/Provenance'
import { usePeople } from '@/api/hooks'

/** BR-W-03. The client offers exactly the edges the domain declares — no more, and no fewer. */
const TRANSITIONS: Record<string, WorkStatus[]> = {
  proposed: ['todo', 'rejected'],
  todo: ['in_progress', 'cancelled'],
  in_progress: ['blocked', 'done', 'cancelled'],
  blocked: ['in_progress', 'cancelled'],
  done: ['in_progress'],
  cancelled: [],
  rejected: [],
}

export function WorkDetail() {
  const { workId = '' } = useParams()
  const work = useWorkItem(workId)
  const people = usePeople()

  if (work.isPending) return <Loading label="this work item" />
  if (work.isError) return <ErrorState error={work.error} retry={() => void work.refetch()} />
  const item = work.data

  return (
    <article aria-labelledby="work-title">
      <h1 id="work-title">{item.title}</h1>
      <dl>
        <dt>Status</dt>
        <dd data-testid="work-status">{item.status}</dd>
        <dt>Partition</dt>
        <dd>{item.project_id === null ? 'Non-project' : 'Project'}</dd>
        <dt>Visibility</dt>
        <dd>{item.visibility}</dd>
        <dt>Started</dt>
        <dd>{item.started_at ?? '—'}</dd>
        <dt>Completed</dt>
        <dd>{item.completed_at ?? '—'}</dd>
      </dl>

      <StatusControls work={item} />
      <Assignments work={item} />
      <Blockers workId={item.id} />
      {/* "Why does this exist?" — the same walk a Commitment gets, because the chain has the same
          shape whatever sits at the end of it (ADR-0056). Work captured by hand says so. */}
      <Provenance
        entityId={item.id}
        people={people.data}
        createdByHand="Captured directly by a person. No AI proposed it."
      />
    </article>
  )
}

function StatusControls({ work }: { work: Work }) {
  const change = useChangeWorkStatus()
  const [reason, setReason] = useState('')
  const allowed = TRANSITIONS[work.status] ?? []

  return (
    <section aria-labelledby="status-heading">
      <h2 id="status-heading">Move this work</h2>
      {allowed.length === 0 ? (
        <Empty>This work has reached a final state.</Empty>
      ) : (
        <>
          {/* BR-W-04: blocking needs a cause, so the field appears with the button that needs it. */}
          {allowed.includes('blocked') ? (
            <label>
              Reason for blocking
              <input value={reason} onChange={(event) => setReason(event.target.value)} />
            </label>
          ) : null}
          <div className="actions">
            {allowed.map((target) => (
              <button
                key={target}
                type="button"
                disabled={change.isPending}
                onClick={() =>
                  change.mutate({
                    work,
                    target,
                    ...(target === 'blocked' && reason ? { blocked_reason: reason } : {}),
                  })
                }
              >
                {target.replace('_', ' ')}
              </button>
            ))}
          </div>
        </>
      )}
      {change.isError ? <ErrorState error={change.error} /> : null}
    </section>
  )
}

function Assignments({ work }: { work: Work }) {
  const assignments = useAssignments(work.id)
  const setOwner = useSetOwner()
  const assign = useAssign()
  const end = useEndAssignment()
  const [owner, setOwnerId] = useState('')
  const [contributor, setContributor] = useState('')

  if (assignments.isPending) return <Loading label="assignments" />
  if (assignments.isError) return <ErrorState error={assignments.error} />

  const rows = assignments.data ?? []
  const active = rows.filter((row) => row.status === 'active')

  return (
    <section aria-labelledby="assignments-heading">
      <h2 id="assignments-heading">Who is on this</h2>

      {active.length === 0 ? (
        // BR-W-15. Unassigned is a valid steady state, not a warning.
        <Empty>Nobody is assigned. That is a complete record, not a gap.</Empty>
      ) : (
        <ul>
          {active.map((row) => (
            <li key={row.id}>
              <span data-testid={`assignment-${row.role}`}>{row.role}</span>{' '}
              <span>{row.person_id}</span>{' '}
              <button
                type="button"
                onClick={() => end.mutate({ workId: work.id, assignment: row })}
              >
                End assignment
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* BR-W-14: ended rows stay, because ownership history is how it stays attributable. */}
      {rows.some((row) => row.status === 'ended') ? (
        <details>
          <summary>Previous assignments</summary>
          <ul>
            {rows
              .filter((row) => row.status === 'ended')
              .map((row) => (
                <li key={row.id}>
                  {row.role} · {row.person_id} · ended {row.ended_at}
                </li>
              ))}
          </ul>
        </details>
      ) : null}

      <PersonPicker label="Owner" value={owner} onChange={setOwnerId} />
      <button
        type="button"
        disabled={!owner || setOwner.isPending}
        onClick={() => setOwner.mutate({ workId: work.id, person_id: owner })}
      >
        Set owner
      </button>

      <PersonPicker label="Add contributor" value={contributor} onChange={setContributor} />
      <button
        type="button"
        disabled={!contributor || assign.isPending}
        onClick={() =>
          assign.mutate({ workId: work.id, person_id: contributor, role: 'CONTRIBUTOR' })
        }
      >
        Add contributor
      </button>

      {setOwner.isError ? <ErrorState error={setOwner.error} /> : null}
      {assign.isError ? <ErrorState error={assign.error} /> : null}
      {end.isError ? <ErrorState error={end.error} /> : null}
    </section>
  )
}

function Blockers({ workId }: { workId: string }) {
  const dependencies = useDependencies(workId)
  if (dependencies.isPending) return <Loading label="dependencies" />
  if (dependencies.isError) return <ErrorState error={dependencies.error} />

  const rows = dependencies.data ?? []
  const blocking = rows.filter((row) => row.blocked_id === workId)
  const blocked = rows.filter((row) => row.blocker_id === workId)

  return (
    <section aria-labelledby="dependencies-heading">
      <h2 id="dependencies-heading">Dependencies</h2>
      {rows.length === 0 ? (
        <Empty>Nothing depends on this, and this depends on nothing.</Empty>
      ) : (
        <>
          <h3>Waiting on</h3>
          <ul data-testid="blocked-by">
            {blocking.map((row) => (
              <li key={row.id}>
                {row.blocker_type}:{row.blocker_id} · {row.status}
              </li>
            ))}
          </ul>
          <h3>Holding up</h3>
          <ul data-testid="blocks">
            {blocked.map((row) => (
              <li key={row.id}>
                {row.blocked_type}:{row.blocked_id} · {row.status}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
