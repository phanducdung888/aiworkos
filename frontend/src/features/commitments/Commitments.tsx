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
  useLinkCommitment,
  useOverdueCommitments,
  usePeople,
  useProjects,
  useWork,
} from '@/api/hooks'
import { Empty, ErrorState, Loading } from '@/components/States'
import { Field } from '@/components/Field'
import { Provenance, nameOf } from '@/components/Provenance'
import { commitmentStatus, duePrecision } from '@/components/vocabulary'
import { Badge } from '@/components/Badge'

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
  captured: 'Đã ghi nhận',
  open: 'Xác nhận',
  fulfilled: 'Đánh dấu hoàn thành',
  missed: 'Đánh dấu lỡ hẹn',
  renegotiated: 'Thương lượng lại',
  cancelled: 'Huỷ',
  disputed: 'Phản đối',
  withdrawn: 'Rút lại',
}

export function due(commitment: Commitment): string {
  // A promise with no date is a complete record, and `vague` is the honest reason it has none: the
  // message named a deadline this system would not guess at (ADR-0055).
  if (commitment.due_date === null) return `Không có hạn (${duePrecision(commitment.due_precision)})`
  if (commitment.due_precision === 'exact') return commitment.due_date
  return `${commitment.due_date} (${duePrecision(commitment.due_precision)})`
}

export function CommitmentList() {
  const [overdueOnly, setOverdueOnly] = useState(false)
  const all = useCommitments()
  const overdue = useOverdueCommitments()
  const people = usePeople()
  const shown = overdueOnly ? overdue : all

  if (shown.isPending) return <Loading label="các lời hứa" />
  if (shown.isError) return <ErrorState error={shown.error} retry={() => void shown.refetch()} />

  return (
    <section aria-labelledby="commitments-heading">
      <h1 id="commitments-heading">Lời hứa</h1>
      <p>
        <label>
          <input
            type="checkbox"
            checked={overdueOnly}
            onChange={(changed) => setOverdueOnly(changed.target.checked)}
          />{' '}
          Chỉ hiện quá hạn
        </label>{' '}
        {/* BR-C-06: asking is not deciding. Nothing is written by looking at this. */}
        <span className="field__hint">
          Quá hạn được tính khi đọc. Lời hứa không có hạn thì không được liệt kê — một hạn chót
          không ai đặt ra thì không thể bị lỡ.
        </span>
      </p>

      {shown.data.length === 0 ? (
        <Empty>{overdueOnly ? 'Không có gì quá hạn.' : 'Chưa ghi nhận lời hứa nào.'}</Empty>
      ) : (
        <table>
          <caption>Những gì mọi người đã nói sẽ làm.</caption>
          <thead>
            <tr>
              <th scope="col">Lời hứa</th>
              <th scope="col">Người hứa</th>
              <th scope="col">Trạng thái</th>
              <th scope="col">Hạn</th>
            </tr>
          </thead>
          <tbody>
            {shown.data.map((commitment) => (
              <tr key={commitment.id}>
                <td>
                  <Link to={`/commitments/${commitment.id}`}>{commitment.statement}</Link>
                </td>
                <td>{nameOf(people.data, commitment.committed_by_person_id)}</td>
                <td>
                  <Badge kind="commitment" value={commitment.status} />
                </td>
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

  if (commitment.isPending) return <Loading label="lời hứa" />
  if (commitment.isError) {
    return <ErrorState error={commitment.error} retry={() => void commitment.refetch()} />
  }

  const promise = commitment.data
  const available = TRANSITIONS[promise.status] ?? []

  return (
    <section aria-labelledby="commitment-heading">
      <h1 id="commitment-heading">{promise.statement}</h1>
      <dl>
        <dt>Người hứa</dt>
        <dd data-testid="committer">{nameOf(people.data, promise.committed_by_person_id)}</dd>
        <dt>Trạng thái</dt>
        <dd data-testid="status">
          <Badge kind="commitment" value={promise.status} />
        </dd>
        <dt>Hạn</dt>
        <dd data-testid="due">{due(promise)}</dd>
        <dt>Độ tin cậy</dt>
        <dd>{promise.confidence}%</dd>
      </dl>

      {available.length > 0 ? (
        <section aria-labelledby="lifecycle-heading">
          <h2 id="lifecycle-heading">Chuyển trạng thái</h2>
          <p className="field__hint">
            Chỉ người hứa, người nhận, hoặc quản lý trong tuyến của họ mới làm được (BR-C-09). Nếu
            bạn không thuộc nhóm đó, thay đổi sẽ bị từ chối.
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
          Lời hứa này đang ở trạng thái {commitmentStatus(promise.status)}. Không còn bước nào
          tiếp theo.
        </p>
      )}

      <Belonging promise={promise} />

      <Provenance
        entityId={promise.id}
        people={people.data}
        createdByHand="Do một người ghi trực tiếp. Không phải AI đề xuất."
      />
    </section>
  )
}

/**
 * Nơi lời hứa thuộc về: công việc nó hoàn thành, và dự án nó phục vụ (CP27).
 *
 * Cả hai đều không bắt buộc, và đó là chủ ý: một lời hứa chưa biết thuộc về đâu vẫn là một bản ghi
 * đầy đủ (BR-C-08). Cho tới CP27 thì cơ sở dữ liệu, dịch vụ và API đều mang được hai liên kết này
 * còn giao diện thì không hỏi — nên trong một tổ chức đang dùng thật, tám lời hứa không trỏ vào đâu cả.
 *
 * Đây là việc của con người chứ không phải của AI. Đoán sai nghĩa là gắn trách nhiệm vào nhầm dự án,
 * và không có gì trong một câu hứa nói rõ nó thuộc công việc nào (BR-AI-17).
 */
function Belonging({ promise }: { promise: Commitment }) {
  const work = useWork()
  const projects = useProjects()
  const link = useLinkCommitment()

  return (
    <section aria-labelledby="belonging-heading">
      <h2 id="belonging-heading">Thuộc về</h2>
      <Field label="Công việc được hoàn thành bởi lời hứa này" hint="Không bắt buộc.">
        {(id) => (
          <select
            id={id}
            data-testid="fulfilling-work"
            value={promise.fulfilling_work_id ?? ''}
            disabled={link.isPending}
            onChange={(event) =>
              link.mutate({ commitment: promise, fulfilling_work_id: event.target.value || null })
            }
          >
            <option value="">Chưa gắn với công việc nào</option>
            {(work.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        )}
      </Field>

      <Field label="Dự án" hint="Không bắt buộc. Lời hứa không thuộc dự án nào vẫn hợp lệ.">
        {(id) => (
          <select
            id={id}
            data-testid="commitment-project"
            value={promise.project_id ?? ''}
            disabled={link.isPending}
            onChange={(event) =>
              link.mutate({ commitment: promise, project_id: event.target.value || null })
            }
          >
            <option value="">Không thuộc dự án nào</option>
            {(projects.data ?? []).map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      {link.isError ? <ErrorState error={link.error} /> : null}
    </section>
  )
}
