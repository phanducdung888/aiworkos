/**
 * Trạng thái của miền, viết ra bằng tiếng Việt.
 *
 * **Chỉ để hiển thị.** Giá trị gửi lên API luôn là chuỗi tiếng Anh mà máy chủ định nghĩa — dịch một
 * giá trị rồi gửi đi là đổi ý nghĩa của yêu cầu, không phải đổi ngôn ngữ của màn hình. CP27 đã suýt
 * mắc lỗi đó: một lượt thay thế hàng loạt đã biến `decision: 'rejected'` trong một bài kiểm thử
 * thành `decision: 'đã từ chối'`, và bài kiểm thử đó bắt được.
 *
 * Một giá trị không có trong bảng thì trả về nguyên trạng. Đoán một bản dịch cho trạng thái mà giao
 * diện chưa biết sẽ che mất việc máy chủ vừa thêm một trạng thái mới.
 */

const COMMITMENT_STATUS: Record<string, string> = {
  captured: 'đã ghi nhận',
  open: 'đang mở',
  fulfilled: 'đã hoàn thành',
  missed: 'đã lỡ hẹn',
  renegotiated: 'đã thương lượng lại',
  cancelled: 'đã huỷ',
  disputed: 'đang tranh chấp',
  withdrawn: 'đã rút lại',
}

const WORK_STATUS: Record<string, string> = {
  proposed: 'đề xuất',
  todo: 'cần làm',
  in_progress: 'đang làm',
  blocked: 'bị chặn',
  done: 'hoàn thành',
  cancelled: 'đã huỷ',
  rejected: 'đã từ chối',
}

const DUE_PRECISION: Record<string, string> = {
  exact: 'chính xác',
  week: 'trong tuần',
  month: 'trong tháng',
  vague: 'chưa rõ',
}

const EXECUTION_STATUS: Record<string, string> = {
  pending: 'đang chờ',
  executed: 'đã thực thi',
  failed: 'thất bại',
  expired: 'đã hết hạn',
  not_applicable: 'không áp dụng',
}

const EVENT_TYPE: Record<string, string> = {
  EXTERNAL_MESSAGE: 'tin nhắn từ bên ngoài',
  MANUAL_CAPTURE: 'ghi nhận thủ công',
  MEETING_NOTE: 'ghi chú cuộc họp',
  COMMENT: 'bình luận',
  ATTACHMENT: 'tệp đính kèm',
  SYSTEM_ACTIVITY: 'hoạt động hệ thống',
}

const ATTACHMENT_STATUS: Record<string, string> = {
  pending: 'đang chờ tải lên',
  available: 'sẵn sàng',
  expired: 'đã quá hạn tải lên',
  purged: 'đã xoá',
}

const PROCESSING_STATUS: Record<string, string> = {
  received: 'chưa phân tích',
  normalised: 'đã chuẩn hoá',
  extracted: 'đã phân tích',
  failed: 'không đọc được',
  skipped: 'bỏ qua',
}

function lookup(table: Record<string, string>, value: string | null | undefined): string {
  if (!value) return ''
  return table[value] ?? value
}

export const commitmentStatus = (v: string | null | undefined): string =>
  lookup(COMMITMENT_STATUS, v)
export const workStatus = (v: string | null | undefined): string => lookup(WORK_STATUS, v)
export const duePrecision = (v: string | null | undefined): string => lookup(DUE_PRECISION, v)
export const executionStatus = (v: string | null | undefined): string =>
  lookup(EXECUTION_STATUS, v)
export const eventType = (v: string | null | undefined): string => lookup(EVENT_TYPE, v)
export const attachmentStatus = (v: string | null | undefined): string =>
  lookup(ATTACHMENT_STATUS, v)

const TARGET_TYPE: Record<string, string> = {
  commitment: 'một lời hứa',
  work: 'một công việc',
  work_assignment: 'một phân công',
  project: 'một dự án',
}

export const targetType = (v: string | null | undefined): string => lookup(TARGET_TYPE, v)

export const processingStatus = (v: string | null | undefined): string =>
  lookup(PROCESSING_STATUS, v)
