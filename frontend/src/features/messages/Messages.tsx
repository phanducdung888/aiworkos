/**
 * Everything that came in, and what the system did with each of them.
 *
 * The screen an operator opens to answer one question: *did that message arrive, and was it read?*
 * Until CP30 there was no way to ask it. 68 Events existed, the API could list them, and no surface
 * did — so "I sent an email and nothing happened" was unanswerable without a database client.
 *
 * **The list is what the caller may read, not what exists.** Filtering happens in SQL through
 * `readable_events`, and an administrator is not thereby entitled to a conversation somebody marked
 * restricted (BR-E-08). This is deliberate and worth stating on the screen, because a review tool
 * that silently omits rows is worse than one that says what it is showing.
 *
 * Nothing here writes. Asking whether a message was processed must never be what processes it.
 */
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useEvent,
  useEvents,
  useProposalsForEvent,
  type EventSummary,
  type ProcessingStatus,
} from "@/api/hooks";
import { Badge } from "@/components/Badge";
import { ErrorState, Loading } from "@/components/States";
import { Field } from "@/components/Field";
import { eventType } from "@/components/vocabulary";

/** The statuses, in the order an operator scans them: trouble first. */
const STATUSES: { value: ProcessingStatus | ""; label: string }[] = [
  { value: "", label: "Tất cả" },
  { value: "failed", label: "Không đọc được" },
  { value: "received", label: "Chưa phân tích" },
  { value: "extracted", label: "Đã phân tích" },
  { value: "skipped", label: "Bỏ qua" },
];

function when(iso: string): string {
  return new Date(iso).toLocaleString("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

export function MessageList() {
  const [status, setStatus] = useState<ProcessingStatus | "">("");
  const events = useEvents(status ? { processing_status: status } : {});

  return (
    <section aria-labelledby="messages-heading">
      <h1 id="messages-heading">Tin nhắn đã nhận</h1>
      <p>
        Mọi thứ đi vào hệ thống, kèm việc AI đã đọc hay chưa. Danh sách này chỉ
        hiển thị những tin nhắn bạn được phép đọc — một cuộc trao đổi được đánh
        dấu hạn chế không xuất hiện ở đây kể cả với quản trị viên.
      </p>

      <Field label="Lọc theo tình trạng">
        {(id) => (
          <select
            id={id}
            value={status}
            onChange={(event) =>
              setStatus(event.target.value as ProcessingStatus | "")
            }
          >
            {STATUSES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        )}
      </Field>

      {events.isPending ? <Loading label="tin nhắn" /> : null}
      {events.isError ? (
        <ErrorState error={events.error} retry={() => void events.refetch()} />
      ) : null}
      {events.data ? <Feed items={events.data} /> : null}
    </section>
  );
}

function Feed({ items }: { items: EventSummary[] }) {
  if (items.length === 0) {
    return (
      <p data-testid="no-messages">Không có tin nhắn nào khớp bộ lọc này.</p>
    );
  }
  return (
    <table data-testid="messages">
      <thead>
        <tr>
          <th>Nhận lúc</th>
          <th>Nội dung</th>
          <th>Nguồn</th>
          <th>Tình trạng</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id}>
            <td className="mono">{when(item.occurred_at)}</td>
            <td>
              <Link to={`/messages/${item.id}`}>
                {item.title || "(không có tiêu đề)"}
              </Link>
              <span className="field__hint"> {eventType(item.type)}</span>
            </td>
            <td className="mono">{item.source_system}</td>
            <td>
              <Badge kind="processing" value={item.processing_status} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function MessageDetail() {
  const { eventId = "" } = useParams();
  const event = useEvent(eventId);
  const proposals = useProposalsForEvent(eventId);

  if (event.isPending) return <Loading label="tin nhắn" />;
  if (event.isError)
    return (
      <ErrorState error={event.error} retry={() => void event.refetch()} />
    );
  if (!event.data) return null;

  const detail = event.data;

  return (
    <section aria-labelledby="message-heading">
      <h1 id="message-heading">{detail.title || "(không có tiêu đề)"}</h1>
      <p>
        <Link to="/messages">← Tất cả tin nhắn</Link>
      </p>

      <dl>
        <div>
          <dt>Tình trạng xử lý</dt>
          <dd>
            <Badge kind="processing" value={detail.processing_status} />
          </dd>
        </div>
        <div>
          <dt>Nhận lúc</dt>
          <dd>{when(detail.occurred_at)}</dd>
        </div>
        <div>
          <dt>Nguồn</dt>
          <dd className="mono">{detail.source_system}</dd>
        </div>
        <div>
          <dt>Loại</dt>
          <dd>{eventType(detail.type)}</dd>
        </div>
      </dl>

      {/* A status of `failed` with no reason sends somebody to the container logs, which is where
          this used to be the only place it lived. */}
      {detail.processing_error ? (
        <p role="alert" className="field__error" data-testid="processing-error">
          {detail.processing_error}
        </p>
      ) : null}

      <section aria-labelledby="body-heading">
        <h2 id="body-heading">Nội dung</h2>
        <blockquote data-testid="body">{detail.body_text}</blockquote>
      </section>

      <section aria-labelledby="produced-heading">
        <h2 id="produced-heading">Kết quả phân tích</h2>
        <Produced status={detail.processing_status} proposals={proposals} />
      </section>
    </section>
  );
}

/**
 * What this message turned into.
 *
 * The three answers are genuinely different and the screen must not collapse them: nothing has read
 * it yet; something read it and found nothing; something tried and could not. Only the middle one
 * is a statement about the message (BR-E-20).
 */
function Produced({
  status,
  proposals,
}: {
  status: string;
  proposals: ReturnType<typeof useProposalsForEvent>;
}) {
  if (status === "received") {
    return <p data-testid="not-read">Chưa có gì đọc tin nhắn này.</p>;
  }
  if (status === "failed") {
    return (
      <p data-testid="read-failed">
        Đã thử đọc nhưng không xong. Lý do ở trên.
      </p>
    );
  }
  if (status === "skipped") {
    return (
      <p data-testid="read-skipped">
        Tin nhắn này không thuộc diện được phân tích.
      </p>
    );
  }
  if (proposals.isPending) return <Loading label="đề xuất" />;
  if (proposals.isError) return <ErrorState error={proposals.error} />;
  const items = proposals.data ?? [];
  if (items.length === 0) {
    return (
      <p data-testid="nothing-found">
        Đã đọc và không tìm thấy lời hứa hay yêu cầu nào. Đây là một kết quả,
        không phải lỗi.
      </p>
    );
  }
  return (
    <ul data-testid="produced">
      {items.map((proposal) => (
        <li key={proposal.id}>
          <Link to={`/proposals/${proposal.id}`}>{proposal.summary}</Link>{" "}
          <span className="field__hint">{proposal.status}</span>
        </li>
      ))}
    </ul>
  );
}
