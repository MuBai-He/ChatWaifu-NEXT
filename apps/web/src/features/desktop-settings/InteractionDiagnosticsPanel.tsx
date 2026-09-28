import type {
  InteractionTraceDetail,
  InteractionTracePage,
  InteractionTraceSummary,
} from "@chatwaifu/protocol";
import { useEffect, useMemo, useState } from "react";

import {
  getInteractionTraceDetail,
  getInteractionTraces,
} from "../chat/runtimeClient";

const TRIGGER_LABELS: Record<InteractionTraceSummary["trigger"], string> = {
  user: "用户输入",
  proactive: "主动问候",
  ignored_voice: "未参与的环境语音",
  proactive_deferred: "主动问候延后",
};

function status(value: string | null | undefined): string {
  return value ?? "未知（无历史事实）";
}

export function InteractionDiagnosticsPanel({
  sessionId,
  runtimeOnline,
}: {
  sessionId: string | null;
  runtimeOnline: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [includeNonparticipation, setIncludeNonparticipation] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [afterSequence, setAfterSequence] = useState(0);
  const request = useMemo(
    () => ({
      sessionId,
      runtimeOnline,
      open,
      refresh,
      cursor,
      includeNonparticipation,
    }),
    [sessionId, runtimeOnline, open, refresh, cursor, includeNonparticipation],
  );
  const [pageState, setPageState] = useState<{
    request: object;
    page: InteractionTracePage | null;
    error: boolean;
  } | null>(null);
  const page = pageState?.request === request ? pageState.page : null;
  const pageError = pageState?.request === request && pageState.error;

  const detailRequest = useMemo(
    () => ({ request, selectedId, afterSequence }),
    [request, selectedId, afterSequence],
  );
  const [detailState, setDetailState] = useState<{
    request: object;
    detail: InteractionTraceDetail | null;
    error: boolean;
  } | null>(null);
  const detail =
    detailState?.request === detailRequest ? detailState.detail : null;
  const detailError =
    detailState?.request === detailRequest && detailState.error;

  useEffect(() => {
    if (!request.open || !request.runtimeOnline || !request.sessionId) return;
    const controller = new AbortController();
    void getInteractionTraces(request.sessionId, {
      cursor: request.cursor,
      includeNonparticipation: request.includeNonparticipation,
      signal: controller.signal,
    }).then(
      (result) => {
        if (!controller.signal.aborted)
          setPageState({ request, page: result, error: false });
      },
      () => {
        if (!controller.signal.aborted)
          setPageState({ request, page: null, error: true });
      },
    );
    return () => controller.abort();
  }, [request]);

  useEffect(() => {
    if (
      !detailRequest.request.open ||
      !detailRequest.request.runtimeOnline ||
      !detailRequest.request.sessionId ||
      !detailRequest.selectedId
    )
      return;
    const controller = new AbortController();
    void getInteractionTraceDetail(
      detailRequest.request.sessionId,
      detailRequest.selectedId,
      { afterSequence: detailRequest.afterSequence, signal: controller.signal },
    ).then(
      (result) => {
        if (!controller.signal.aborted)
          setDetailState({
            request: detailRequest,
            detail: result,
            error: false,
          });
      },
      () => {
        if (!controller.signal.aborted)
          setDetailState({ request: detailRequest, detail: null, error: true });
      },
    );
    return () => controller.abort();
  }, [detailRequest]);

  return (
    <details
      className="interaction-diagnostics-panel"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>高级诊断：单次互动</summary>
      {open ? (
        <div>
          <p>
            按互动查看触发、规划、记忆、工具及实际交付的元数据。历史缺失显示未知。
          </p>
          <div className="desktop-settings-danger-row">
            <label>
              <input
                type="checkbox"
                checked={includeNonparticipation}
                onChange={(event) => {
                  setIncludeNonparticipation(event.target.checked);
                  setCursor(null);
                  setSelectedId(null);
                }}
              />
              包含未参与与延后记录
            </label>
            <button
              type="button"
              onClick={() => setRefresh((value) => value + 1)}
            >
              刷新诊断
            </button>
          </div>
          {!sessionId || !runtimeOnline ? (
            <p>连接到当前会话后可查看诊断。</p>
          ) : pageError ? (
            <p role="alert">读取互动诊断失败，请刷新重试。</p>
          ) : !page ? (
            <p role="status">正在读取互动…</p>
          ) : page.items?.length ? (
            <>
              <ul aria-label="互动诊断列表">
                {page.items.map((item) => (
                  <li key={item.interaction_id}>
                    <button
                      type="button"
                      aria-pressed={selectedId === item.interaction_id}
                      onClick={() => {
                        setSelectedId(item.interaction_id);
                        setAfterSequence(0);
                      }}
                    >
                      {new Date(item.occurred_at).toLocaleString()} ·{" "}
                      {TRIGGER_LABELS[item.trigger]} ·{" "}
                      {status(item.generation_state ?? item.reason)}
                    </button>
                  </li>
                ))}
              </ul>
              {page.has_more && page.next_cursor ? (
                <button
                  type="button"
                  onClick={() => {
                    setCursor(page.next_cursor ?? null);
                    setSelectedId(null);
                  }}
                >
                  查看更早互动
                </button>
              ) : null}
            </>
          ) : (
            <p>暂无可显示的互动。</p>
          )}

          {selectedId ? (
            <section aria-label="单次互动详情">
              {detailError ? (
                <p role="alert">读取该次互动失败，请重新选择。</p>
              ) : !detail ? (
                <p role="status">正在读取详情…</p>
              ) : (
                <>
                  <h3>互动 {detail.summary.interaction_id.slice(0, 8)}</h3>
                  <p>
                    触发：{TRIGGER_LABELS[detail.summary.trigger]}；原因：
                    {status(detail.summary.reason)}；生成：
                    {status(detail.summary.generation_state)}
                  </p>
                  <p>
                    上下文版本：{status(detail.prompt_identity?.identity_hash)}
                    ；模型：{status(detail.prompt_identity?.chat_route.model)}
                    ；模板：
                    {status(detail.prompt_identity?.prompt_template_version)}
                  </p>
                  <p>
                    计划：{status(detail.response_plan?.intent)} /{" "}
                    {status(detail.response_plan?.tone)}；预算：
                    {detail.prompt_budget
                      ? `${detail.prompt_budget.used}/${detail.prompt_budget.budget}`
                      : "未知（无历史事实）"}
                  </p>
                  <p>
                    记忆候选：{detail.memory_candidates?.length ?? 0}
                    ；实际选中：
                    {detail.selected_memory_ids === null
                      ? "未知（旧事件）"
                      : (detail.selected_memory_ids?.length ?? 0)}
                  </p>
                  {detail.memory_candidates?.length ? (
                    <ul aria-label="记忆候选与选中">
                      {detail.memory_candidates.map((item) => (
                        <li key={item.memory_id}>
                          {item.memory_id.slice(0, 8)} ·{" "}
                          {item.selected_for_prompt ? "已注入" : "仅候选"} ·{" "}
                          {item.currently_visible
                            ? "当前可见"
                            : "已删除或不可见"}
                          {item.score === null ? "" : ` · 分数 ${item.score}`}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <p>
                    工具：{detail.tool_calls?.length ?? 0} 次；渠道交付：
                    {status(detail.delivery_status)}；播放 ACK：
                    {detail.playback_segments?.length
                      ? "见分段状态"
                      : "未知（无历史事实）"}
                  </p>
                  {detail.tool_calls?.length ? (
                    <ul aria-label="工具状态">
                      {detail.tool_calls.map((item, index) => (
                        <li key={item.tool_call_id ?? index}>
                          {item.status} ·{" "}
                          {item.duration_ms === null
                            ? "耗时未知"
                            : `${item.duration_ms} ms`}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {detail.delivery_parts?.length ? (
                    <ul aria-label="交付分条状态">
                      {detail.delivery_parts.map((item) => (
                        <li key={item.part_id}>
                          {item.ordinal + 1}. {item.kind} · {item.status} ·{" "}
                          {item.required ? "必需" : "可选"}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {detail.playback_segments?.length ? (
                    <ul aria-label="播放分段确认">
                      {detail.playback_segments.map((item) => (
                        <li key={item.segment_id}>
                          {item.segment_index + 1}. 客户端 ACK：{item.state} ·
                          进度 {item.played_pts_ms} ms
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <ol aria-label="互动事件时间线">
                    {detail.timeline?.map((item) => (
                      <li key={item.sequence}>
                        {item.sequence} · {item.event_type} ·{" "}
                        {status(item.status ?? item.reason)}
                      </li>
                    ))}
                  </ol>
                  {detail.truncated ? (
                    <small>结果已截断，请按游标继续查看。</small>
                  ) : null}
                  {detail.next_cursor !== null &&
                  detail.next_cursor !== undefined ? (
                    <button
                      type="button"
                      onClick={() => setAfterSequence(detail.next_cursor ?? 0)}
                    >
                      继续读取事件
                    </button>
                  ) : null}
                </>
              )}
            </section>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}
