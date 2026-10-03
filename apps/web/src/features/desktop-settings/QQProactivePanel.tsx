import { useEffect, useRef, useState } from "react";
import { parseChannelProactivePolicy } from "@chatwaifu/protocol";
import {
  cancelChannelOutboundIntent,
  getChannelOutboundIntents,
  getChannelProactivePolicy,
  previewChannelProactivePolicy,
  updateChannelProactivePolicy,
  type ChannelOutboundIntentPage,
  type ChannelOutboundIntentSnapshot,
  type ChannelProactivePolicy,
  type ChannelProactivePolicySnapshot,
  type ChannelProactivePreview,
} from "../chat/runtime-client/channelProactiveClient";
import { isConflictError } from "../chat/runtime-client/realtimeClient";
import { SettingsToggle } from "./SettingsPrimitives";
import "./qq-proactive-panel.css";

type Props = {
  connectionId: string;
  runtimeOnline: boolean;
  connectionVerified: boolean;
};
type Operation = "save" | "preview" | "history" | "cancel";
type Draft = Omit<
  ChannelProactivePolicy,
  "idle_minutes" | "cooldown_minutes" | "daily_budget" | "ttl_minutes"
> & {
  idle_minutes: string;
  cooldown_minutes: string;
  daily_budget: string;
  ttl_minutes: string;
};

export function QQProactivePanel(props: Props) {
  return <QQProactiveContent key={props.connectionId} {...props} />;
}

function QQProactiveContent({
  connectionId,
  runtimeOnline,
  connectionVerified,
}: Props) {
  const active = runtimeOnline && connectionVerified;
  const [lastActive, setLastActive] = useState(active);
  const [snapshot, setSnapshot] =
    useState<ChannelProactivePolicySnapshot | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [fresh, setFresh] = useState(false);
  const [pageFresh, setPageFresh] = useState(false);
  const [page, setPage] = useState<ChannelOutboundIntentPage | null>(null);
  const [preview, setPreview] = useState<ChannelProactivePreview | null>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [readRevision, setReadRevision] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  const operationRef = useRef<Operation | null>(null);

  // Reconnection must invalidate permission before an effect starts its read.
  if (lastActive !== active) {
    setLastActive(active);
    setFresh(false);
    setPageFresh(false);
    setOperation(null);
    setPreview(null);
  }

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    operationRef.current = null;
    void Promise.allSettled([
      getChannelProactivePolicy(connectionId, controller.signal),
      getChannelOutboundIntents(connectionId, undefined, controller.signal),
    ]).then(([policyResult, pageResult]) => {
      if (controller.signal.aborted) return;
      if (
        policyResult.status === "fulfilled" &&
        policyResult.value.connection_id === connectionId
      ) {
        setSnapshot(policyResult.value);
        setDraft(toDraft(policyResult.value.policy));
        setFresh(true);
      } else {
        setNotice("无法确认 QQ 主动文字设置，请刷新后再管理。");
      }
      if (
        pageResult.status === "fulfilled" &&
        pageResult.value.items.every(
          (item) => item.connection_id === connectionId,
        )
      ) {
        setPage(pageResult.value);
        setPageFresh(true);
      } else {
        setNotice("近期请求状态未确认，请刷新后再取消。");
      }
    });
    return () => {
      controller.abort();
      if (controllerRef.current === controller) controllerRef.current = null;
    };
  }, [active, connectionId, readRevision]);

  const reload = () => {
    controllerRef.current?.abort();
    operationRef.current = null;
    setOperation(null);
    setFresh(false);
    setPageFresh(false);
    setPreview(null);
    setReadRevision((value) => value + 1);
  };
  const available = active && fresh && Boolean(snapshot?.binding_id);
  const disabled = !available || operation !== null;
  let policy: ChannelProactivePolicy | null = null;
  if (draft) {
    try {
      policy = parseChannelProactivePolicy({
        ...draft,
        idle_minutes: numberValue(draft.idle_minutes),
        cooldown_minutes: numberValue(draft.cooldown_minutes),
        daily_budget: numberValue(draft.daily_budget),
        ttl_minutes: numberValue(draft.ttl_minutes),
      });
    } catch {
      /* Invalid drafts never reach the update API. */
    }
  }
  const dirty = Boolean(
    policy &&
    snapshot &&
    JSON.stringify(policy) !== JSON.stringify(snapshot.policy),
  );

  const run = async (
    kind: Operation,
    action: (signal: AbortSignal) => Promise<void>,
  ) => {
    const controller = controllerRef.current;
    if (
      !available ||
      operationRef.current ||
      !controller ||
      controller.signal.aborted
    )
      return;
    operationRef.current = kind;
    setOperation(kind);
    setNotice(null);
    try {
      await action(controller.signal);
    } catch (error: unknown) {
      if (controller.signal.aborted) return;
      if (isConflictError(error)) {
        setNotice("版本已变化，正在刷新最新状态；请核对后重新操作。");
        reload();
      } else {
        setNotice("操作结果未确认，请刷新最新状态后再管理。");
        // A timed-out mutation may have committed. Never retain a stale permission.
        reload();
      }
    } finally {
      if (!controller.signal.aborted) {
        operationRef.current = null;
        setOperation(null);
      }
    }
  };
  const save = () => {
    if (!policy || !snapshot) return;
    const savedPolicy = policy;
    const revision = snapshot.revision;
    void run("save", async (signal) => {
      const result = await updateChannelProactivePolicy(
        connectionId,
        savedPolicy,
        revision,
        signal,
      );
      if (signal.aborted) return;
      if (result.connection_id !== connectionId)
        throw new Error("connection mismatch");
      setSnapshot(result);
      setDraft(toDraft(result.policy));
      setPreview(null);
      setPageFresh(false);
      const history = await getChannelOutboundIntents(
        connectionId,
        undefined,
        signal,
      );
      if (signal.aborted) return;
      if (history.items.some((item) => item.connection_id !== connectionId))
        throw new Error("connection mismatch");
      setPage(history);
      setPageFresh(true);
      setNotice(
        result.policy.enabled
          ? "QQ 主动文字设置已保存。"
          : "已关闭 QQ 主动文字；实际投递事实仍保留在近期记录中。",
      );
    });
  };
  const checkEligibility = () => {
    void run("preview", async (signal) => {
      const result = await previewChannelProactivePolicy(connectionId, signal);
      if (signal.aborted) return;
      if (
        result.connection_id !== connectionId ||
        result.policy_revision !== snapshot?.revision ||
        result.binding_id !== snapshot.binding_id
      ) {
        setNotice("设置版本已变化，正在刷新；资格检查没有生成或发送消息。");
        reload();
        return;
      }
      setPreview(result);
    });
  };
  const readHistory = (cursor?: string) => {
    void run("history", async (signal) => {
      const result = await getChannelOutboundIntents(
        connectionId,
        cursor,
        signal,
      );
      if (signal.aborted) return;
      if (result.items.some((item) => item.connection_id !== connectionId))
        throw new Error("connection mismatch");
      setPage(result);
      setPageFresh(true);
    });
  };
  const cancel = (intent: ChannelOutboundIntentSnapshot) => {
    if (!pageFresh || !intent.cancelable) return;
    void run("cancel", async (signal) => {
      const result = await cancelChannelOutboundIntent(
        connectionId,
        intent.request_id,
        intent.revision,
        signal,
      );
      if (signal.aborted) return;
      if (
        result.connection_id !== connectionId ||
        result.request_id !== intent.request_id
      )
        throw new Error("intent mismatch");
      setPage((current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.request_id === result.request_id ? result : item,
              ),
            }
          : current,
      );
      setPreview(null);
      setNotice("请求状态已更新，实际投递结果见下方；已发送的消息无法撤回。");
    });
  };
  const updateDraft = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  };

  return (
    <section className="qq-proactive-panel" aria-label="QQ 主动文字">
      <h3>主动文字问候</h3>
      <p>
        仅向已配对的主人私聊发送文字，默认关闭；桌面主动陪伴开关不会开启 QQ
        问候。
      </p>
      <p>每次保存都会停止旧待发请求，保存后的新主人消息才开始计算空闲时间。</p>
      {snapshot ? (
        <p>
          已保存状态：{snapshot.policy.enabled ? "已开启" : "关闭"}
          {dirty ? "（有尚未保存的改动）" : ""}
        </p>
      ) : null}
      <p>
        收件人：
        {snapshot?.binding_id
          ? "当前已配对主人（固定目标）"
          : "尚未确认主人绑定"}
      </p>
      {draft ? (
        <>
          <SettingsToggle
            label="允许角色主动发送 QQ 文字"
            description="修改开关后须保存才生效。关闭会停止尚未发送的请求，不撤回已发送消息。"
            checked={draft.enabled}
            disabled={disabled}
            onChange={(value) => updateDraft("enabled", value)}
          />
          <fieldset disabled={disabled} className="qq-proactive-fields">
            <legend>问候时间与频率</legend>
            <label>
              时区（IANA）
              <input
                aria-label="QQ 问候时区"
                value={draft.timezone}
                onChange={(event) =>
                  updateDraft("timezone", event.currentTarget.value)
                }
              />
            </label>
            {(
              [
                ["idle_minutes", "空闲等待（分钟）", 1440],
                ["cooldown_minutes", "问候间隔（分钟）", 10080],
                ["daily_budget", "每日上限（次）", 20],
                ["ttl_minutes", "问候有效期（分钟）", 60],
              ] as const
            ).map(([key, label, max]) => (
              <label key={key}>
                {label}
                <input
                  aria-label={label}
                  type="number"
                  min={1}
                  max={max}
                  step={1}
                  value={draft[key]}
                  onChange={(event) =>
                    updateDraft(key, event.currentTarget.value)
                  }
                />
              </label>
            ))}
          </fieldset>
          <SettingsToggle
            label="QQ 问候安静时段"
            description="安静时段只限制主动问候，你仍可正常私聊。"
            checked={draft.quiet_hours_enabled}
            disabled={disabled}
            onChange={(value) => updateDraft("quiet_hours_enabled", value)}
          />
          <fieldset disabled={disabled} className="qq-proactive-fields">
            <legend>安静时段</legend>
            <label>
              开始
              <input
                aria-label="QQ 安静时段开始"
                type="time"
                value={draft.quiet_start}
                onChange={(event) =>
                  updateDraft("quiet_start", event.currentTarget.value)
                }
              />
            </label>
            <label>
              结束
              <input
                aria-label="QQ 安静时段结束"
                type="time"
                value={draft.quiet_end}
                onChange={(event) =>
                  updateDraft("quiet_end", event.currentTarget.value)
                }
              />
            </label>
          </fieldset>
          {!policy ? (
            <p role="alert">
              请填写有效时区、时间和范围内的整数；有效期最多 60 分钟。
            </p>
          ) : null}
        </>
      ) : (
        <p role="status">
          {active
            ? "正在读取主动文字设置…"
            : "Runtime 离线或 QQ 连接状态尚未确认。"}
        </p>
      )}
      {!active || !fresh || !snapshot?.binding_id ? (
        <p role="status">
          设置尚未确认，管理操作暂不可用。请恢复连接或重新确认主人绑定后刷新。
        </p>
      ) : null}
      <div className="qq-channel-actions">
        <button
          type="button"
          className="channels-settings-primary-action"
          disabled={disabled || !policy || !dirty}
          onClick={save}
        >
          {operation === "save" ? "正在保存…" : "保存 QQ 问候设置"}
        </button>
        <button
          type="button"
          className="qq-channel-secondary-action"
          disabled={disabled || !policy || dirty}
          onClick={checkEligibility}
        >
          {operation === "preview" ? "正在检查…" : "检查主动问候资格"}
        </button>
        <button
          type="button"
          className="qq-channel-secondary-action"
          disabled={!active || operation !== null}
          onClick={reload}
        >
          刷新主动文字状态
        </button>
      </div>
      <p>
        资格检查使用已保存设置，不生成文字、不占用预算、不发送消息。修改后请先保存。
      </p>
      {preview ? (
        <div className="qq-proactive-preview" role="status">
          <strong>{reasonLabel(preview.reason)}</strong>
          <p>
            今日已预留 {preview.reserved_today} 次，剩余{" "}
            {preview.remaining_daily_budget} 次。取消或失败也不会退还次数。
          </p>
          <p>
            检查时间：
            {formatDate(preview.evaluated_at, snapshot?.policy.timezone)}
          </p>
          {preview.last_owner_at ? (
            <p>
              最近主人输入：
              {formatDate(preview.last_owner_at, snapshot?.policy.timezone)}
            </p>
          ) : null}
          {preview.next_eligible_at ? (
            <p>
              下一资格时间：
              {formatDate(preview.next_eligible_at, snapshot?.policy.timezone)}
            </p>
          ) : null}
          {preview.last_reserved_at ? (
            <p>
              最近预留：
              {formatDate(preview.last_reserved_at, snapshot?.policy.timezone)}
            </p>
          ) : null}
          {preview.pending_request_id ? (
            <p>当前已有未结束的主动请求，详情见近期记录。</p>
          ) : null}
          {preview.expires_at ? (
            <p>
              本次空闲窗口截止：
              {formatDate(preview.expires_at, snapshot?.policy.timezone)}
            </p>
          ) : null}
        </div>
      ) : null}
      <h4>近期主动请求</h4>
      <p>显示请求状态与投递事实；服务端接受不代表手机已读。</p>
      {!pageFresh && page ? (
        <p role="status">以下为上次记录，刷新前不可取消。</p>
      ) : null}
      {page?.items.length ? (
        <ul className="qq-proactive-intents">
          {page.items.map((intent) => (
            <li key={intent.request_id}>
              <strong>{intentStatus(intent)}</strong>
              <p>
                {formatDate(intent.created_at, snapshot?.policy.timezone)} ·
                截止 {formatDate(intent.expires_at, snapshot?.policy.timezone)}
              </p>
              {intent.reply_text ? (
                <p className="qq-proactive-reply">{intent.reply_text}</p>
              ) : null}
              <p>
                投递：{deliveryStatus(intent)}
                {intent.provider_receipt_present
                  ? "；已记录服务端接受回执，无法撤回"
                  : "；尚无服务端接受回执"}
              </p>
              {intent.cancel_requested_at ? (
                <p>已请求停止未发送部分。</p>
              ) : null}
              {intent.cancelable ? (
                <button
                  type="button"
                  className="qq-channel-secondary-action"
                  disabled={disabled || !pageFresh}
                  onClick={() => cancel(intent)}
                >
                  停止未发送请求
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p>{pageFresh ? "暂无主动请求。" : "近期请求状态待刷新。"}</p>
      )}
      <div className="qq-channel-actions">
        <button
          type="button"
          className="qq-channel-secondary-action"
          disabled={disabled}
          onClick={() => readHistory()}
        >
          读取最近 25 条
        </button>
        {page?.next_cursor ? (
          <button
            type="button"
            className="qq-channel-secondary-action"
            disabled={disabled || !pageFresh}
            onClick={() => readHistory(page.next_cursor ?? undefined)}
          >
            查看更早请求
          </button>
        ) : null}
      </div>
      {notice ? <p role="status">{notice}</p> : null}
    </section>
  );
}

function toDraft(policy: ChannelProactivePolicy): Draft {
  return {
    ...policy,
    idle_minutes: String(policy.idle_minutes),
    cooldown_minutes: String(policy.cooldown_minutes),
    daily_budget: String(policy.daily_budget),
    ttl_minutes: String(policy.ttl_minutes),
  };
}
function numberValue(value: string): number {
  return value.trim() ? Number(value) : NaN;
}
function formatDate(value: string, timeZone = "Asia/Shanghai"): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}
function reasonLabel(reason: ChannelProactivePreview["reason"]): string {
  const labels: Record<typeof reason, string> = {
    eligible: "当前符合问候资格，检查不会触发发送",
    disabled: "主动文字已关闭",
    unsupported_provider: "此渠道不支持主动文字",
    connection_unavailable: "QQ 连接暂不可用",
    owner_binding_required: "需要确认主人绑定",
    no_owner_activity: "保存设置后尚无新的主人消息",
    idle_threshold_not_reached: "尚未达到空闲等待时间",
    idle_window_expired: "本次空闲窗口已过期",
    quiet_hours: "当前处于安静时段",
    conversation_busy: "对话正在进行",
    cooldown_active: "尚在问候间隔内",
    daily_budget_exhausted: "今日问候次数已用完",
    episode_already_reserved: "本次空闲窗口已有请求",
    capacity_reached: "待处理请求已达上限",
  };
  return labels[reason];
}
function intentStatus(intent: ChannelOutboundIntentSnapshot): string {
  if (intent.status !== "settled")
    return {
      pending: "等待处理",
      generating: "正在生成文字",
      planned: "已安排投递",
    }[intent.status];
  const reasons: Record<string, string> = {
    cancelled: "已停止请求",
    operator_cancelled: "已停止请求",
    expired: "已过期",
    delivered: "请求已结束",
    superseded: "新主人输入已停止请求",
  };
  return (
    (intent.settled_reason && reasons[intent.settled_reason]) || "请求已结束"
  );
}
function deliveryStatus(intent: ChannelOutboundIntentSnapshot): string {
  if (!intent.delivery_status) return "尚未建立投递";
  return {
    pending: "等待投递",
    sending: "发送中，结果尚待确认",
    delivered: "服务端已接受",
    failed: "投递结束，未确认成功",
    cancelled: "未发送部分已停止",
  }[intent.delivery_status];
}
