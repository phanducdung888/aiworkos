/**
 * All Work, split the way the reporting rules require.
 *
 * The partition is not a filter somebody forgot to set. BR-RPT-01 says All Work *is* Project Work
 * plus Non-project Work, and BR-RPT-04 says a figure that does not name its partition is ambiguous,
 * so both counts are shown and neither is the default view of the other.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useWork } from '@/api/hooks'
import type { WorkFilters, WorkStatus } from '@/api/hooks'
import { Empty, ErrorState, Loading } from '@/components/States'
import { Field } from '@/components/Field'
import { workStatus } from '@/components/vocabulary'

const STATUSES: WorkStatus[] = [
  'proposed',
  'todo',
  'in_progress',
  'blocked',
  'done',
  'cancelled',
  'rejected',
]

export function WorkList() {
  const [status, setStatus] = useState<WorkStatus | ''>('')
  const filters = useMemo<WorkFilters>(() => (status ? { status } : {}), [status])
  const work = useWork(filters)

  const counts = useMemo(() => {
    const items = work.data ?? []
    const project = items.filter((item) => item.project_id !== null).length
    return { project, nonProject: items.length - project, total: items.length }
  }, [work.data])

  if (work.isPending) return <Loading label="công việc" />
  if (work.isError) return <ErrorState error={work.error} retry={() => void work.refetch()} />

  const items = work.data ?? []

  return (
    <section aria-labelledby="work-heading">
      <h1 id="work-heading">Công việc</h1>

      {/* BR-RPT-01 and BR-RPT-04: All Work, and which partition each figure covers. */}
      <dl className="partitions">
        <div>
          <dt>Tất cả công việc</dt>
          <dd data-testid="count-total">{counts.total}</dd>
        </div>
        <div>
          <dt>Thuộc một dự án</dt>
          <dd data-testid="count-project">{counts.project}</dd>
        </div>
        <div>
          <dt>Không thuộc dự án nào</dt>
          <dd data-testid="count-non-project">{counts.nonProject}</dd>
        </div>
      </dl>

      <Field label="Trạng thái">
        {(id) => (
          <select
            id={id}
            value={status}
            onChange={(event) => setStatus(event.target.value as WorkStatus | '')}
          >
            <option value="">Mọi trạng thái</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value.replace('_', ' ')}
              </option>
            ))}
          </select>
        )}
      </Field>

      {items.length === 0 ? (
        <Empty>Chưa có công việc nào khớp với bộ lọc này.</Empty>
      ) : (
        <table>
          <caption className="visually-hidden">Danh sách công việc</caption>
          <thead>
            <tr>
              <th scope="col">Tiêu đề</th>
              <th scope="col">Trạng thái</th>
              <th scope="col">Phân nhóm</th>
              <th scope="col">Due</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <th scope="row">
                  <Link to={`/work/${item.id}`}>{item.title}</Link>
                </th>
                <td>{workStatus(item.status)}</td>
                <td>{item.project_id === null ? 'Ngoài dự án' : 'Trong dự án'}</td>
                <td>{item.due_date ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
