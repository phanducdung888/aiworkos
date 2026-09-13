/**
 * Một trạng thái, đọc được và nhìn thấy được.
 *
 * Màu ở đây nói lên một điều duy nhất: **việc này có cần ai đó làm gì không?** Đỏ là đã hỏng, hổ
 * phách là đang chờ một người, xanh là đã xong, xám là đang trôi bình thường. Không tô màu theo
 * kiểu mỗi trạng thái một sắc — bảy màu thì không còn màu nào có nghĩa.
 *
 * Chữ vẫn là chữ đầy đủ chứ không phải chỉ một chấm màu: người mù màu đọc được, và ảnh chụp màn
 * hình dán vào một cuộc trao đổi vẫn còn ý nghĩa.
 */
import {
  attachmentStatus,
  commitmentStatus,
  executionStatus,
  workStatus,
} from '@/components/vocabulary'

type Kind = 'commitment' | 'work' | 'execution' | 'attachment'

/** Trạng thái nào đáng để mắt dừng lại, theo từng loại. */
const TONE: Record<Kind, Record<string, 'danger' | 'warn' | 'good'>> = {
  commitment: {
    missed: 'danger',
    disputed: 'danger',
    captured: 'warn',
    open: 'warn',
    fulfilled: 'good',
  },
  work: {
    blocked: 'danger',
    rejected: 'danger',
    in_progress: 'warn',
    done: 'good',
  },
  execution: {
    failed: 'danger',
    expired: 'danger',
    pending: 'warn',
    executed: 'good',
  },
  attachment: {
    expired: 'danger',
    pending: 'warn',
    available: 'good',
  },
}

const LABEL: Record<Kind, (value: string) => string> = {
  commitment: commitmentStatus,
  work: workStatus,
  execution: executionStatus,
  attachment: attachmentStatus,
}

export function Badge({ kind, value }: { kind: Kind; value: string | null | undefined }) {
  if (!value) return null
  const tone = TONE[kind][value]
  return (
    <span className={tone ? `badge badge--${tone}` : 'badge'}>{LABEL[kind](value)}</span>
  )
}
