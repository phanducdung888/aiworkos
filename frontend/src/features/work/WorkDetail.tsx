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
import { Link, useParams } from 'react-router-dom'
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
import { Provenance, nameOf } from '@/components/Provenance'
import { useCommitments, usePeople } from '@/api/hooks'
import { commitmentStatus, workStatus } from '@/components/vocabulary'

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

  if (work.isPending) return <Loading label="công việc này" />
  if (work.isError) return <ErrorState error={work.error} retry={() => void work.refetch()} />
  const item = work.data

  return (
    <article aria-labelledby="work-title">
      <h1 id="work-title">{item.title}</h1>
      <dl>
        <dt>Trạng thái</dt>
        <dd data-testid="work-status">{workStatus(item.status)}</dd>
        <dt>Phân nhóm</dt>
        <dd>{item.project_id === null ? 'Ngoài dự án' : 'Trong dự án'}</dd>
        <dt>Phạm vi xem</dt>
        <dd>{item.visibility}</dd>
        <dt>Bắt đầu</dt>
        <dd>{item.started_at ?? '—'}</dd>
        <dt>Hoàn thành</dt>
        <dd>{item.completed_at ?? '—'}</dd>
      </dl>

      <StatusControls work={item} />
      <Assignments work={item} />
      <Blockers workId={item.id} />
      <Promises workId={item.id} />
      {/* "Why does this exist?" — the same walk a Commitment gets, because the chain has the same
          shape whatever sits at the end of it (ADR-0056). Work captured by hand says so. */}
      <Provenance
        entityId={item.id}
        people={people.data}
        createdByHand="Do một người ghi trực tiếp. Không phải AI đề xuất."
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
      <h2 id="status-heading">Chuyển trạng thái</h2>
      {allowed.length === 0 ? (
        <Empty>Công việc này đã ở trạng thái cuối.</Empty>
      ) : (
        <>
          {/* BR-W-04: blocking needs a cause, so the field appears with the button that needs it. */}
          {allowed.includes('blocked') ? (
            <label>
              Lý do bị chặn
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

  if (assignments.isPending) return <Loading label="phân công" />
  if (assignments.isError) return <ErrorState error={assignments.error} />

  const rows = assignments.data ?? []
  const active = rows.filter((row) => row.status === 'active')

  return (
    <section aria-labelledby="assignments-heading">
      <h2 id="assignments-heading">Ai đang làm</h2>

      {active.length === 0 ? (
        // BR-W-15. Unassigned is a valid steady state, not a warning.
        <Empty>Chưa phân công ai. Đó là một bản ghi đầy đủ, không phải thiếu sót.</Empty>
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
                Kết thúc phân công
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* BR-W-14: ended rows stay, because ownership history is how it stays attributable. */}
      {rows.some((row) => row.status === 'ended') ? (
        <details>
          <summary>Phân công trước đây</summary>
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

      <PersonPicker label="Người phụ trách" value={owner} onChange={setOwnerId} />
      <button
        type="button"
        disabled={!owner || setOwner.isPending}
        onClick={() => setOwner.mutate({ workId: work.id, person_id: owner })}
      >
        Đặt người phụ trách
      </button>

      <PersonPicker label="Thêm người tham gia" value={contributor} onChange={setContributor} />
      <button
        type="button"
        disabled={!contributor || assign.isPending}
        onClick={() =>
          assign.mutate({ workId: work.id, person_id: contributor, role: 'CONTRIBUTOR' })
        }
      >
        Thêm người tham gia
      </button>

      {setOwner.isError ? <ErrorState error={setOwner.error} /> : null}
      {assign.isError ? <ErrorState error={assign.error} /> : null}
      {end.isError ? <ErrorState error={end.error} /> : null}
    </section>
  )
}

function Blockers({ workId }: { workId: string }) {
  const dependencies = useDependencies(workId)
  if (dependencies.isPending) return <Loading label="phụ thuộc" />
  if (dependencies.isError) return <ErrorState error={dependencies.error} />

  const rows = dependencies.data ?? []
  const blocking = rows.filter((row) => row.blocked_id === workId)
  const blocked = rows.filter((row) => row.blocker_id === workId)

  return (
    <section aria-labelledby="dependencies-heading">
      <h2 id="dependencies-heading">Phụ thuộc</h2>
      {rows.length === 0 ? (
        <Empty>Không có gì phụ thuộc vào việc này, và việc này không phụ thuộc vào gì.</Empty>
      ) : (
        <>
          <h3>Đang chờ</h3>
          <ul data-testid="blocked-by">
            {blocking.map((row) => (
              <li key={row.id}>
                {row.blocker_type}:{row.blocker_id} · {row.status}
              </li>
            ))}
          </ul>
          <h3>Đang chặn</h3>
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


/**
 * Những lời hứa mà công việc này hoàn thành (CP27).
 *
 * Chiều còn lại của `commitment.fulfilling_work_id`. Liên kết vẫn thuộc về lời hứa — một công việc
 * không "sở hữu" lời hứa nào cả — nên ở đây chỉ đọc, và việc gắn hay gỡ được làm ở màn hình lời hứa.
 *
 * Có mặt ở đây vì một câu hỏi rất thật: "ai đã hứa gì về việc này?" Trước CP27 câu đó không trả lời
 * được trên trình duyệt, dù API đã lọc được theo `fulfilling_work_id` từ Phase 1.
 */
function Promises({ workId }: { workId: string }) {
  const promises = useCommitments({ fulfilling_work_id: workId })
  const people = usePeople()

  if (promises.isPending) return <Loading label="các lời hứa" />
  if (promises.isError) {
    // Một mục phụ thì hỏng lặng lẽ. `ErrorState` ở đây tạo ra báo động thứ hai cạnh báo động
    // thật của màn hình — người đọc phải tự đoán cái nào mới là việc họ vừa làm.
    return (
      <section aria-labelledby="promises-heading">
        <h2 id="promises-heading">Lời hứa về việc này</h2>
        <p className="field__hint">Không đọc được danh sách lời hứa lúc này.</p>
      </section>
    )
  }

  return (
    <section aria-labelledby="promises-heading">
      <h2 id="promises-heading">Lời hứa về việc này</h2>
      {promises.data.length === 0 ? (
        <Empty>
          Chưa có lời hứa nào gắn với công việc này. Việc gắn được làm ở màn hình lời hứa.
        </Empty>
      ) : (
        <ul data-testid="work-promises">
          {promises.data.map((promise) => (
            <li key={promise.id}>
              <Link to={`/commitments/${promise.id}`}>{promise.statement}</Link>{' '}
              <span className="field__hint">
                {nameOf(people.data, promise.committed_by_person_id)} · {commitmentStatus(promise.status)}
                {promise.due_date ? ` · hạn ${promise.due_date}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
