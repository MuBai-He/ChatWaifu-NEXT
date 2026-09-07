import { useEffect, useMemo, useState } from "react";

import {
  getStickerUsage,
  type StickerUsageHistory,
  type StickerUsageRecord,
} from "../chat/runtimeClient";

const STATUS_LABELS: Record<StickerUsageRecord["status"], string> = {
  pending: "等待发送",
  sending: "发送中",
  delivered: "已送达",
  failed: "发送失败",
  cancelled: "已取消",
  skipped: "已跳过",
};

export function StickerUsagePanel({
  characterId,
  runtimeOnline,
  refreshToken,
}: {
  characterId: string;
  runtimeOnline: boolean;
  refreshToken: object | null;
}) {
  const [open, setOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const request = useMemo(
    () => ({ characterId, runtimeOnline, refreshToken, open, refresh }),
    [characterId, runtimeOnline, refreshToken, open, refresh],
  );
  const [result, setResult] = useState<{
    request: object;
    history: StickerUsageHistory | null;
    error: boolean;
  } | null>(null);
  const current = result?.request === request ? result : null;
  const history = current?.history ?? null;
  const error = current?.error ?? false;
  const items = history?.items ?? [];

  useEffect(() => {
    if (!request.open || !request.runtimeOnline) return;
    const controller = new AbortController();
    void getStickerUsage(request.characterId, controller.signal).then(
      (history) => {
        if (!controller.signal.aborted)
          setResult({ request, history, error: false });
      },
      () => {
        if (!controller.signal.aborted)
          setResult({ request, history: null, error: true });
      },
    );
    return () => controller.abort();
  }, [request]);

  return (
    <details
      className="sticker-usage-panel"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>最近表情发送记录</summary>
      {open ? (
        <>
          <p>仅已送达的表情算作成功使用。删除表情后，这里也会移除对应记录。</p>
          <button
            type="button"
            className="sticker-library-refresh-button"
            disabled={!runtimeOnline}
            onClick={() => setRefresh((value) => value + 1)}
          >
            刷新发送记录
          </button>
          {!runtimeOnline ? (
            <p>连接后可查看发送记录。</p>
          ) : error ? (
            <p role="alert">读取发送记录失败，请重试。</p>
          ) : !history ? (
            <p role="status">正在读取发送记录…</p>
          ) : items.length === 0 ? (
            <p>暂无可显示的发送记录。</p>
          ) : (
            <ul aria-label="表情发送记录">
              {items.map((item) => (
                <li key={item.part_id}>
                  <div>
                    <strong>{item.label}</strong>
                    <span>{STATUS_LABELS[item.status]}</span>
                    {item.attempt > 1 ? (
                      <span>尝试 {item.attempt} 次</span>
                    ) : null}
                  </div>
                  <time dateTime={item.delivered_at ?? item.updated_at}>
                    {new Date(
                      item.delivered_at ?? item.updated_at,
                    ).toLocaleString()}
                  </time>
                </li>
              ))}
            </ul>
          )}
          {history?.has_more ? (
            <small>这里只显示最近保留的一部分记录。</small>
          ) : null}
        </>
      ) : null}
    </details>
  );
}
