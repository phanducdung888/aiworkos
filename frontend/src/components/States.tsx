/**
 * Loading, empty, error and permission-denied — the four states a surface must have on purpose.
 *
 * They live together because they are one decision, not four: a screen that handles loading and
 * forgets permission-denied renders an empty list to somebody who is not allowed to see the data,
 * which reads as "there is nothing here" and is a different and worse claim (DoD §3).
 */
import type { ReactNode } from 'react'
import { ApiProblem } from '@/api/problem'

export function Loading({ label }: { label: string }) {
  return (
    <p role="status" aria-live="polite" className="state state--loading">
      Đang tải {label}…
    </p>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="state state--empty">{children}</p>
}

/**
 * An error a person can read.
 *
 * Permission denied is called out by name rather than folded into a generic failure, because "you
 * may not" and "something went wrong" ask for different things from the reader: one is a fact about
 * their access, the other is an invitation to retry.
 */
export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const problem = error instanceof ApiProblem ? error : null

  if (problem?.isForbidden) {
    return (
      <div role="alert" className="state state--forbidden">
        <p>Bạn không có quyền xem mục này.</p>
        <p className="state__hint">
          Quyền ở đây đến từ vai trò của bạn trong tổ chức, không phải từ trang này.
        </p>
      </div>
    )
  }

  return (
    <div role="alert" className="state state--error">
      <p>{problem ? problem.message_for_humans : 'Đã có lỗi xảy ra.'}</p>
      {problem?.rule ? <p className="state__hint">Quy tắc {problem.rule}</p> : null}
      {retry ? (
        <button type="button" onClick={retry}>
          Thử lại
        </button>
      ) : null}
    </div>
  )
}
