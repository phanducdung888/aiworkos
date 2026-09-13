/**
 * Naming the organization to work in.
 *
 * The app has always said "Choose the organization you are working in." and, until CP25, offered
 * nothing to choose with: `setOrganizationId` existed and no component called it. A real sign-in
 * therefore ended on a sentence, having made no API call at all.
 *
 * **The identifier is typed, not listed, and that is the design rather than a shortcut.** Which
 * organizations a person belongs to is a question no signed-in caller can ask here: the token
 * carries no organization claim on purpose (security-model §2 — a claim keeps asserting a
 * membership after it is revoked), and every table that could answer it is under RLS keyed on
 * `app_current_org()`, so a session with no organization context sees nothing anywhere. Answering
 * it would need either a claim or a `BYPASSRLS` role, and both reverse a decision this system made
 * deliberately (ADR-0008, ADR-0031). `app/platform/principal.py` already states the rule this
 * implements: the organization arrives explicitly and is never inferred, "not from a single
 * membership, not from a default".
 *
 * What it must not do is fail silently. An identifier that is not yours produces a 404 on every
 * subsequent request, which reads as an empty product rather than as a wrong answer — so it is
 * checked here, once, against `/api/v1/me`, and only stored if it resolves.
 */
import { useState } from 'react'
import { api } from '@/api/client'
import { ApiProblem } from '@/api/problem'
import { Field } from '@/components/Field'
import { useAuth } from '@/auth/AuthProvider'

/** What went wrong, in the words of the person who typed it. */
function explain(error: unknown): string {
  if (error instanceof ApiProblem) {
    if (error.status === 404) {
      return 'Không có tổ chức nào mang mã đó có bạn là thành viên đang hoạt động. Hãy kiểm tra lại mã, hoặc hỏi người đã tạo tài khoản cho bạn.'
    }
    if (error.status === 403) {
      return 'Bạn là thành viên của tổ chức đó nhưng chưa được giao vai trò nào. Cần một người có quyền quản trị cấp vai trò cho bạn.'
    }
    if (error.status === 400) {
      return 'Mã này không hợp lệ. Nó có dạng 01a093df-3607-707d-8342-f155ece35b0a.'
    }
  }
  return error instanceof Error ? error.message : 'Không kiểm tra được tổ chức.'
}

export function ChooseOrganization() {
  const auth = useAuth()
  const [value, setValue] = useState('')
  const [checking, setChecking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault()
    const candidate = value.trim()
    if (!candidate) return
    setChecking(true)
    setProblem(null)
    try {
      // Deliberately not through the configured client's organization getter: this identifier is
      // not committed to anything yet, and storing it before it is known to work is what produces
      // an app that is permanently broken until somebody clears their browser storage.
      const { error } = await api.GET('/api/v1/me', {
        headers: { 'X-Organization-Id': candidate },
      })
      if (error) throw error
      auth.setOrganizationId(candidate)
    } catch (cause) {
      setProblem(explain(cause))
    } finally {
      setChecking(false)
    }
  }

  return (
    <main className="gate">
      <section className="gate__card" aria-labelledby="choose-org-heading">
        <span className="brand brand--large">
          <span className="brand__mark" aria-hidden="true" />
          AI WorkOS
        </span>
        <h2 id="choose-org-heading">Chọn tổ chức bạn đang làm việc</h2>
        <p>
          WorkOS không bao giờ tự đoán mục này. Mã tổ chức được hiển thị khi tổ chức được tạo; sau
          khi kiểm tra hợp lệ, trình duyệt này sẽ ghi nhớ.
        </p>
        <form onSubmit={(event) => void submit(event)}>
          <Field
            label="Mã tổ chức"
            hint="Một mã UUID, ví dụ 01a093df-3607-707d-8342-f155ece35b0a."
          >
            {(id) => (
              <input
                id={id}
                required
                value={value}
                spellCheck={false}
                autoComplete="off"
                onChange={(event) => setValue(event.target.value)}
                placeholder="01a093df-3607-707d-8342-f155ece35b0a"
              />
            )}
          </Field>
          {problem ? (
            <p role="alert" className="field__error">
              {problem}
            </p>
          ) : null}
          <button type="submit" className="button button--primary" disabled={checking || !value.trim()}>
            {checking ? 'Đang kiểm tra…' : 'Tiếp tục'}
          </button>
        </form>
        <p>
          <button type="button" className="button button--quiet" onClick={auth.signOut}>
            Đăng xuất
          </button>
        </p>
      </section>
    </main>
  )
}
