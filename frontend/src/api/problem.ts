/**
 * The error shape the API actually returns (RFC 9457).
 *
 * Modelled as a class rather than a plain object so that `catch` can tell an API refusal from a
 * network failure or a bug in this app. The distinction matters at the UI edge: a 403 is a sentence
 * we show the user, a dropped connection is a retry, and a TypeError is ours to fix.
 */
export class ApiProblem extends Error {
  readonly status: number
  readonly type: string
  readonly title: string
  readonly detail: string
  /** Business rule id, present on 422 rule violations (e.g. `BR-W-03`). */
  readonly rule?: string
  readonly errors?: { loc: (string | number)[]; msg: string }[]

  constructor(status: number, body: unknown) {
    const problem = (body ?? {}) as Record<string, unknown>
    const detail = typeof problem.detail === 'string' ? problem.detail : 'The request failed.'
    super(detail)
    this.name = 'ApiProblem'
    this.status = status
    this.type = typeof problem.type === 'string' ? problem.type : 'urn:workos:error:unknown'
    this.title = typeof problem.title === 'string' ? problem.title : 'Request failed'
    this.detail = detail
    if (typeof problem.rule === 'string') this.rule = problem.rule
    if (Array.isArray(problem.errors)) {
      this.errors = problem.errors as ApiProblem['errors']
    }
  }

  /** 403. The caller is known and not permitted this. */
  get isForbidden(): boolean {
    return this.status === 403
  }

  /** 404. Absent, or in another organization — the API does not distinguish, and neither do we. */
  get isNotFound(): boolean {
    return this.status === 404
  }

  /** 412. Somebody else changed the row since we read it (BR-G-06). */
  get isStale(): boolean {
    return this.status === 412
  }

  /**
   * A sentence to put in front of a person.
   *
   * Rule violations already carry one written for a human — the domain layer composes them with the
   * rule id — so they are shown as they arrive. The rest get a phrasing that says what to do next,
   * because "Precondition Failed" tells a user nothing they can act on.
   */
  get message_for_humans(): string {
    if (this.status === 412) {
      return 'Người khác đã thay đổi mục này trong lúc bạn đang sửa. Tải lại để xem bản của họ.'
    }
    if (this.status === 403) return 'Bạn không có quyền thực hiện việc này.'
    if (this.status === 404) return 'Mục này không còn nữa.'
    if (this.status === 428) return 'Không áp dụng được thay đổi này. Tải lại rồi thử lại.'
    return this.detail
  }
}
