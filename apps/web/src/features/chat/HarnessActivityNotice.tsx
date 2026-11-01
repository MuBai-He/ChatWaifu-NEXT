import { useEffect, useState } from "react";
import { getHarnessActivitySnapshot } from "./runtime-client/harnessClient";

/** Owner-scoped activity continues independently of this conversation generation. */
export function HarnessActivityNotice({
  sessionId,
}: {
  sessionId: string | null;
}) {
  return sessionId ? (
    <OwnerActivityNotice key={sessionId} sessionId={sessionId} />
  ) : null;
}
function OwnerActivityNotice({ sessionId }: { sessionId: string }) {
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let disposed = false,
      inFlight = false,
      cursor = 0,
      initialized = false,
      epoch = "",
      summaryAt = Date.now(),
      pending: string[] = [];
    const refresh = async () => {
      if (inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      try {
        const page = await getHarnessActivitySnapshot(sessionId);
        if (disposed) return;
        if (epoch && epoch !== page.context_epoch) {
          setNotice("");
          pending = [];
        }
        epoch = page.context_epoch;
        if (!initialized) {
          initialized = true;
          cursor = page.next_cursor;
          return;
        }
        const changed = page.items.filter((item) => item.sequence > cursor);
        cursor = Math.max(cursor, page.next_cursor);
        const urgent = changed.filter(
          (item) =>
            item.kind === "waiting" ||
            item.kind === "action.event" ||
            item.state === "needs_verification",
        );
        pending.push(
          ...changed
            .filter(
              (item) =>
                item.kind.startsWith("action.") && !urgent.includes(item),
            )
            .map((item) => item.summary),
        );
        if (!page.quiet && urgent.length)
          setNotice(
            urgent
              .map((item) => item.summary)
              .slice(-3)
              .join("\n"),
          );
        if (!page.quiet && Date.now() - summaryAt >= 300000 && pending.length) {
          setNotice(pending.slice(-3).join("\n"));
          pending = [];
          summaryAt = Date.now();
        }
      } catch {
        /* Connection UI owns availability errors; old notifications expire on context changes. */
      } finally {
        inFlight = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [sessionId]);
  if (!notice) return null;
  return (
    <aside
      className="harness-activity-notice"
      aria-label="宁宁行动通知"
      role="status"
    >
      <p>{notice}</p>
      <small>完整依据和进度见“能力与任务”中的行动时间线。</small>
      <button onClick={() => setNotice("")}>关闭通知</button>
    </aside>
  );
}
