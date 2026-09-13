/**
 * The files a message arrived with.
 *
 * The bytes never pass through the API (ADR-0039): asking for one returns a short-lived URL signed
 * for the store, and the browser fetches it directly. So this is a button rather than a link — the
 * URL does not exist until it is asked for, and minting one on render would issue a credential for
 * every file on the page whether or not anybody wanted it.
 *
 * `rel="noopener"` because the URL carries its own authority; a tab opened with a reference back to
 * this one is a tab that can read the opener's origin.
 */
import { useState } from 'react'
import { useAttachmentContent } from '@/api/hooks'
import type { EventAttachment } from '@/api/hooks'
import { ApiProblem } from '@/api/problem'
import { attachmentStatus } from '@/components/vocabulary'

function size(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function Attachments({
  eventId,
  attachments,
}: {
  eventId: string
  attachments: EventAttachment[]
}) {
  const content = useAttachmentContent(eventId)
  const [problem, setProblem] = useState<string | null>(null)

  if (attachments.length === 0) return null

  // `pending` is a reservation whose upload never completed. Saying so is the honest answer: the
  // record exists and the bytes do not, and offering a button would produce a 409 nobody expects.
  const open = (attachmentId: string): void => {
    setProblem(null)
    content.mutate(attachmentId, {
      onSuccess: ({ download_url }) => {
        window.open(download_url, '_blank', 'noopener,noreferrer')
      },
      onError: (error) => {
        setProblem(
          error instanceof ApiProblem && error.status === 409
            ? 'Tệp này chưa tải lên xong, nên chưa có gì để mở.'
            : 'Không mở được tệp.',
        )
      },
    })
  }

  return (
    <section aria-labelledby="attachments-heading" data-testid="attachments">
      <h3 id="attachments-heading">Tệp đính kèm</h3>
      <ul>
        {attachments.map((attachment) => (
          <li key={attachment.id}>
            {attachment.status === 'available' ? (
              <button type="button" onClick={() => open(attachment.id)}>
                {attachment.filename}
              </button>
            ) : (
              <span>{attachment.filename}</span>
            )}{' '}
            <span className="field__hint">
              {attachment.media_type}
              {size(attachment.size_bytes) ? ` · ${size(attachment.size_bytes)}` : ''}
              {attachment.status === 'available' ? '' : ` · ${attachmentStatus(attachment.status)}`}
            </span>
          </li>
        ))}
      </ul>
      {problem ? (
        <p role="alert" className="field__error">
          {problem}
        </p>
      ) : null}
    </section>
  )
}
