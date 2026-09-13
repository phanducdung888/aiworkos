/**
 * Opening a file that arrived on a message (CP25).
 *
 * The bytes never pass through the API (ADR-0039), so "open" means: ask for a short-lived URL,
 * then hand it to the browser. What matters here is that the URL is asked for at the moment
 * somebody wants it and not before, and that a reservation whose upload never finished says so
 * instead of offering a button that cannot work.
 */
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Attachments } from '@/components/Attachments'
import type { EventAttachment } from '@/api/hooks'
import { renderSurface, stubApi } from '@/test/harness'

const EVENT = '33333333-3333-4333-8333-333333333333'
const FILE = '44444444-4444-4444-8444-444444444444'

function anAttachment(overrides: Record<string, unknown> = {}): EventAttachment {
  return {
    id: FILE,
    event_id: EVENT,
    filename: 'NDA ký kết.pdf',
    media_type: 'application/pdf',
    size_bytes: 4070,
    checksum: '"abc123"',
    status: 'available',
    uploaded_by_person_id: null,
    created_at: '2026-09-13T08:00:00Z',
    completed_at: '2026-09-13T08:00:01Z',
    version: 1,
    ...overrides,
  }
}

const CONTENT = {
  match: `GET /api/v1/events/${EVENT}/attachments/${FILE}/content`,
  body: {
    attachment: anAttachment(),
    download_url: 'http://localhost:9002/workos-attachments/org/x/y?X-Amz-Signature=abc',
    expires_at: '2026-09-13T08:15:00Z',
  },
}

describe('Attachments', () => {
  it('renders nothing at all when the message carried no files', () => {
    stubApi([])
    renderSurface(<Attachments eventId={EVENT} attachments={[]} />)
    expect(screen.queryByTestId('attachments')).toBeNull()
  })

  it('asks for a URL only when somebody asks to open the file', async () => {
    const { calls } = stubApi([CONTENT])
    const opened = vi.fn()
    vi.stubGlobal('open', opened)

    renderSurface(<Attachments eventId={EVENT} attachments={[anAttachment()]} />)
    // Rendering the list must not mint a credential for every file on the page.
    expect(calls.filter((call) => call.url.includes('/content'))).toHaveLength(0)

    await userEvent.click(screen.getByRole('button', { name: 'NDA ký kết.pdf' }))

    await waitFor(() => expect(opened).toHaveBeenCalled())
    const [url, , features] = opened.mock.calls[0] as [string, string, string]
    expect(url).toContain('localhost:9002')
    // Not `_self`, and never with a handle back to this origin.
    expect(features).toContain('noopener')
  })

  it('shows a name a person recognises, with its type and size', () => {
    stubApi([CONTENT])
    renderSurface(<Attachments eventId={EVENT} attachments={[anAttachment()]} />)
    expect(screen.getByText(/application\/pdf/)).toBeInTheDocument()
    expect(screen.getByText(/4 KB/)).toBeInTheDocument()
  })

  it('does not offer to open a file whose upload never finished', () => {
    stubApi([CONTENT])
    renderSurface(
      <Attachments
        eventId={EVENT}
        attachments={[anAttachment({ status: 'pending', size_bytes: null })]}
      />,
    )
    expect(screen.queryByRole('button', { name: 'NDA ký kết.pdf' })).toBeNull()
    expect(screen.getByText(/đang chờ tải lên/)).toBeInTheDocument()
  })

  it('explains a refusal rather than failing silently', async () => {
    stubApi([{ ...CONTENT, status: 409, body: { status: 409, title: 'Refused', detail: 'no' } }])
    renderSurface(<Attachments eventId={EVENT} attachments={[anAttachment()]} />)
    await userEvent.click(screen.getByRole('button', { name: 'NDA ký kết.pdf' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/chưa tải lên xong/i)
  })
})
