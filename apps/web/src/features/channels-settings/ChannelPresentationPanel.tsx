import { effectiveChannelPresentation } from "./channelPresentationDefaults";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { parseChannelPresentationPolicy } from "@chatwaifu/protocol";
import {
  assertRuntimeRequestContext,
  getRuntimeContextRevision,
  readRuntimeRequestContext,
  subscribeRuntimeContext,
  type RuntimeRequestContext,
} from "../chat/runtimeEndpoint";
import {
  updateChannelConnection,
  type ChannelConnectionSnapshot,
} from "../chat/runtimeClient";
import { SettingsToggle } from "../settings/SettingsPrimitives";
import { ChannelSettingsDisclosure } from "./ChannelSettingsPrimitives";

const fields = [
  { key: "max_parts", label: "最多消息段数", min: 1, max: 10 },
  {
    key: "preferred_chars_per_part",
    label: "建议每段字符数",
    min: 10,
    max: 500,
  },
  { key: "soft_max_chars_per_part", label: "每段柔性上限", min: 20, max: 1000 },
  { key: "min_delay_ms", label: "最短段间间隔（毫秒）", min: 0, max: 10000 },
  { key: "max_delay_ms", label: "最长段间间隔（毫秒）", min: 0, max: 30000 },
  {
    key: "total_cadence_delay_ceiling_ms",
    label: "单次累计间隔上限（毫秒）",
    min: 0,
    max: 60000,
  },
] as const;

type Props = {
  connection: ChannelConnectionSnapshot;
  editable: boolean;
  onSaved: (connection: ChannelConnectionSnapshot) => void;
  onRefresh: () => void;
};

export function ChannelPresentationPanel(props: Props) {
  const epoch = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  return (
    <PresentationEditor
      key={`${epoch}:${props.connection.configuration.connection_id}:${props.connection.revision}`}
      {...props}
    />
  );
}
function PresentationEditor({
  connection,
  editable,
  onSaved,
  onRefresh,
}: Props) {
  const initial = effectiveChannelPresentation(connection);
  const [draft, setDraft] = useState(initial);
  const [numbers, setNumbers] = useState(
    Object.fromEntries(fields.map((f) => [f.key, String(initial[f.key])])),
  );
  const [name, setName] = useState(connection.configuration.name);
  const [timeout, setTimeout] = useState(
    String(connection.configuration.timeout_seconds ?? 120),
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const originRef = useRef<RuntimeRequestContext | null>(null);
  const [originReady, setOriginReady] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void readRuntimeRequestContext()
      .then((origin) => {
        if (controller.signal.aborted) return;
        assertRuntimeRequestContext(origin);
        originRef.current = origin;
        setOriginReady(true);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setNotice("无法确认当前连接，请刷新后再修改。");
      });
    return () => {
      controller.abort();
      controllerRef.current?.abort();
      originRef.current = null;
    };
  }, []);
  useEffect(() => {
    if (!editable) controllerRef.current?.abort();
  }, [editable]);
  let policy: ReturnType<typeof parseChannelPresentationPolicy> | null = null;
  try {
    policy = parseChannelPresentationPolicy({
      ...draft,
      ...Object.fromEntries(
        Object.entries(numbers).map(([k, v]) => [
          k,
          v.trim() ? Number(v) : NaN,
        ]),
      ),
    });
    if (
      (policy.soft_max_chars_per_part ?? 120) <
        (policy.preferred_chars_per_part ?? 60) ||
      (policy.max_delay_ms ?? 3000) < (policy.min_delay_ms ?? 800)
    )
      policy = null;
  } catch {
    /* Invalid drafts cannot change a delivery policy. */
  }
  const valid = Boolean(
    policy &&
    name.trim() &&
    name.trim().length <= 128 &&
    timeout.trim() &&
    Number(timeout) > 0 &&
    Number(timeout) <= 600,
  );
  const dirty = Boolean(
    policy &&
    (JSON.stringify(policy) !== JSON.stringify(initial) ||
      name.trim() !== connection.configuration.name ||
      Number(timeout) !== (connection.configuration.timeout_seconds ?? 120)),
  );
  const disabled = !editable || !originReady || busy;
  const save = async () => {
    if (disabled || controllerRef.current || !valid || !policy) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setNotice(null);
    try {
      const expectedContext = originRef.current;
      if (!expectedContext) throw new Error("Runtime context unavailable");
      assertRuntimeRequestContext(expectedContext);
      controller.signal.throwIfAborted();
      const result = await updateChannelConnection(
        connection.configuration.connection_id,
        {
          ...connection.configuration,
          name: name.trim(),
          timeout_seconds: Number(timeout),
          presentation_policy: policy,
        },
        connection.revision,
        controller.signal,
        { expectedContext },
      );
      controller.signal.throwIfAborted();
      assertRuntimeRequestContext(expectedContext);
      if (
        result.configuration.connection_id !==
          connection.configuration.connection_id ||
        result.configuration.account_key !==
          connection.configuration.account_key
      )
        throw new Error("connection mismatch");
      onSaved(result);
    } catch {
      if (!controller.signal.aborted) {
        setNotice("保存结果未确认，请刷新并核对；不会自动重试。");
        onRefresh();
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setBusy(false);
    }
  };
  const instant = draft.profile === "instant_message";
  return (
    <ChannelSettingsDisclosure
      title="回复样式与连接选项"
      description="分段、间隔、正在输入与连接名称"
    >
      <fieldset className="channel-settings-fields" disabled={disabled}>
        <legend>基本选项</legend>
        <label>
          连接名称
          <input
            value={name}
            maxLength={128}
            onChange={(e) => setName(e.currentTarget.value)}
          />
        </label>
        <label>
          回复形式
          <select
            value={draft.profile}
            onChange={(e) =>
              setDraft({
                ...draft,
                profile: e.currentTarget.value as
                  "instant_message" | "single_text",
              })
            }
          >
            <option value="instant_message">即时消息 · 分段发送</option>
            <option value="single_text">合并成一条文字</option>
          </select>
        </label>
      </fieldset>
      <SettingsToggle
        label="分段之间保留自然间隔"
        description="只在分段发送时生效，关闭后连续发送。"
        checked={draft.cadence_enabled ?? true}
        disabled={disabled || !instant}
        onChange={(value) => setDraft({ ...draft, cadence_enabled: value })}
      />
      <SettingsToggle
        label="代码和详细答案保留完整结构"
        description="技术、代码或有结构的长回答可合并发送；不会截断内容来凑段数。"
        checked={draft.bypass_long_form ?? true}
        disabled={disabled || !instant}
        onChange={(value) => setDraft({ ...draft, bypass_long_form: value })}
      />
      <SettingsToggle
        label="显示正在输入"
        description={
          connection.capabilities?.supports_typing
            ? "向支持的客户端显示输入状态，不影响回复生成。"
            : "当前渠道没有原生输入状态接口。"
        }
        checked={draft.typing_enabled ?? false}
        disabled={disabled || !connection.capabilities?.supports_typing}
        onChange={(value) => setDraft({ ...draft, typing_enabled: value })}
      />
      <fieldset
        className="channel-settings-fields"
        disabled={disabled || !instant}
      >
        <legend>分段与发送节奏</legend>
        {fields.map((field) => (
          <label key={field.key}>
            {field.label}
            <input
              aria-label={field.label}
              type="number"
              min={field.min}
              max={field.max}
              step={1}
              value={numbers[field.key]}
              onChange={(e) =>
                setNumbers({ ...numbers, [field.key]: e.currentTarget.value })
              }
            />
            <small>
              {field.min}–{field.max}
            </small>
          </label>
        ))}
      </fieldset>
      <fieldset className="channel-settings-fields" disabled={disabled}>
        <legend>连接活动状态</legend>
        <label>
          活动判定窗口（秒）
          <input
            type="number"
            min={0.1}
            max={600}
            step={0.1}
            value={timeout}
            onChange={(e) => setTimeout(e.currentTarget.value)}
          />
          <small>用于显示最近活动，不是模型超时。</small>
        </label>
      </fieldset>
      <p>
        这些设置只影响外部消息展示。保存 QQ
        连接选项后，群路由会依照现有规则暂停，需要重新核对成员后启用。
      </p>
      {!valid ? (
        <p role="alert">
          请检查数值范围：柔性上限不能小于建议长度，最长间隔不能小于最短间隔。
        </p>
      ) : null}
      <button
        type="button"
        className="channels-settings-primary-action"
        disabled={disabled || !valid || !dirty}
        onClick={() => void save()}
      >
        {busy ? "正在保存…" : "保存回复样式"}
      </button>
      {notice ? <p role="status">{notice}</p> : null}
    </ChannelSettingsDisclosure>
  );
}
