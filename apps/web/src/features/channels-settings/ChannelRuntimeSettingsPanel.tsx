import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  parseChannelRuntimePolicy,
  type ChannelRuntimePolicy,
  type ChannelRuntimeSettingsResponse,
  type GroupDiscussionPolicy,
} from "@chatwaifu/protocol";
import {
  assertRuntimeRequestContext,
  getRuntimeContextRevision,
  readRuntimeRequestContext,
  subscribeRuntimeContext,
} from "../chat/runtimeEndpoint";
import {
  getChannelRuntimeSettings,
  updateChannelRuntimeSettings,
  type ChannelSettingsRequestOptions,
} from "../chat/runtime-client/channelSettingsClient";
import { RuntimeRequestError } from "../chat/runtime-client/http";
import { SettingsToggle } from "../settings/SettingsPrimitives";
import { ChannelSettingsDisclosure } from "./ChannelSettingsPrimitives";

type NumericKey = Exclude<keyof GroupDiscussionPolicy, "enabled">;
type Field = {
  key: NumericKey;
  label: string;
  min: number;
  max: number;
  step?: number;
};
const limits: { title: string; fields: Field[] }[] = [
  {
    title: "缓存与公平分配",
    fields: [
      { key: "max_groups", label: "最多缓存群数", min: 1, max: 128 },
      { key: "message_characters", label: "单条字符上限", min: 80, max: 2000 },
      { key: "cache_messages", label: "每群缓存条数", min: 32, max: 512 },
      {
        key: "cache_characters",
        label: "每群缓存字符数",
        min: 3200,
        max: 128000,
      },
      { key: "retention_seconds", label: "保留时间（秒）", min: 30, max: 3600 },
      { key: "member_messages", label: "每人缓存条数", min: 1, max: 128 },
      {
        key: "member_characters",
        label: "每人缓存字符数",
        min: 80,
        max: 32000,
      },
    ],
  },
  {
    title: "刷屏与重复限制",
    fields: [
      {
        key: "member_messages_per_window",
        label: "每人每窗口消息数",
        min: 1,
        max: 30,
      },
      {
        key: "frequency_window_seconds",
        label: "频率窗口（秒）",
        min: 1,
        max: 300,
      },
      {
        key: "duplicate_window_seconds",
        label: "重复文本窗口（秒）",
        min: 1,
        max: 900,
      },
    ],
  },
  {
    title: "模型输入与按需摘要",
    fields: [
      {
        key: "input_tokens",
        label: "旁听输入预算（参考 token）",
        min: 128,
        max: 8192,
      },
      {
        key: "summary_input_tokens",
        label: "摘要输入预算（参考 token）",
        min: 256,
        max: 16384,
      },
      {
        key: "summary_output_tokens",
        label: "摘要输出预算（参考 token）",
        min: 64,
        max: 1024,
      },
      {
        key: "summary_timeout_seconds",
        label: "摘要超时（秒）",
        min: 0.1,
        max: 30,
        step: 0.1,
      },
    ],
  },
];

export function ChannelRuntimeSettingsPanel({
  runtimeOnline,
}: {
  runtimeOnline: boolean;
}) {
  const epoch = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  return (
    <ChannelRuntimeSettingsContent key={epoch} runtimeOnline={runtimeOnline} />
  );
}

function ChannelRuntimeSettingsContent({
  runtimeOnline,
}: {
  runtimeOnline: boolean;
}) {
  const [snapshot, setSnapshot] =
    useState<ChannelRuntimeSettingsResponse | null>(null);
  const [draft, setDraft] = useState<ChannelRuntimePolicy | null>(null);
  const [numbers, setNumbers] = useState<Partial<Record<NumericKey, string>>>(
    {},
  );
  const [fresh, setFresh] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [readRevision, setReadRevision] = useState(0);
  const requestRef = useRef<ChannelSettingsRequestOptions | null>(null);
  const savingRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = null;
    if (runtimeOnline)
      void (async () => {
        const expectedContext = await readRuntimeRequestContext();
        controller.signal.throwIfAborted();
        setFresh(false);
        setBusy(false);
        savingRef.current = false;
        const options = { expectedContext, signal: controller.signal };
        requestRef.current = options;
        const result = await getChannelRuntimeSettings(options);
        controller.signal.throwIfAborted();
        assertRuntimeRequestContext(expectedContext);
        setSnapshot(result);
        setDraft(result.policy);
        setNumbers(
          Object.fromEntries(
            limits.flatMap((g) =>
              g.fields.map((f) => [
                f.key,
                String(result.policy.group_discussion?.[f.key]),
              ]),
            ),
          ),
        );
        setFresh(true);
      })().catch((error: unknown) => {
        if (!controller.signal.aborted)
          setNotice(
            error instanceof RuntimeRequestError && error.status === 404
              ? "此 Runtime 尚未提供渠道高级设置接口，请更新后端后再修改。现有连接与群管理仍可使用。"
              : "无法确认渠道权限，请刷新后再修改。",
          );
      });
    return () => {
      controller.abort();
      requestRef.current = null;
    };
  }, [runtimeOnline, readRevision]);

  let policy: ChannelRuntimePolicy | null = null;
  try {
    if (draft)
      policy = parseChannelRuntimePolicy({
        ...draft,
        group_discussion: {
          ...draft.group_discussion,
          ...Object.fromEntries(
            Object.entries(numbers).map(([key, value]) => [
              key,
              value.trim() ? Number(value) : NaN,
            ]),
          ),
        },
      });
  } catch {
    /* Incomplete numeric drafts cannot become permissions. */
  }
  const dirty = Boolean(
    policy &&
    snapshot &&
    JSON.stringify(policy) !== JSON.stringify(snapshot.policy),
  );
  const disabled = !runtimeOnline || !fresh || busy;
  const toggle = (
    key: Exclude<keyof ChannelRuntimePolicy, "group_discussion">,
    value: boolean,
  ) => setDraft((old) => (old ? { ...old, [key]: value } : old));
  const save = async () => {
    const options = requestRef.current;
    if (
      disabled ||
      savingRef.current ||
      !policy ||
      !snapshot ||
      !options ||
      options.signal.aborted
    )
      return;
    savingRef.current = true;
    setBusy(true);
    setNotice(null);
    try {
      assertRuntimeRequestContext(options.expectedContext);
      const result = await updateChannelRuntimeSettings(
        policy,
        snapshot.revision,
        options,
      );
      options.signal.throwIfAborted();
      assertRuntimeRequestContext(options.expectedContext);
      setSnapshot(result);
      setDraft(result.policy);
      setNotice(
        "渠道设置已保存到当前 Runtime。新消息使用新预算；进行中的对话保留已冻结资料，未发送的 QQ 私聊语音仍会检查最新权限。",
      );
    } catch {
      if (!options.signal.aborted) {
        setFresh(false);
        setNotice(
          "保存结果未确认，正在读取最新版本；不会自动重试修改。请核对后再保存。",
        );
        setReadRevision((value) => value + 1);
      }
    } finally {
      if (!options.signal.aborted) {
        savingRef.current = false;
        setBusy(false);
      }
    }
  };

  return (
    <section
      className="channels-settings-card channel-runtime-settings"
      aria-label="渠道权限与高级设置"
    >
      <header className="channel-settings-section-heading">
        <h2>渠道权限与高级设置</h2>
        <p>
          作用于当前连接的 Runtime。下面的 QQ 私聊开关适用于该 Runtime
          已配对的主人，群权限单独管理。
        </p>
      </header>
      <div className="channel-settings-body">
        {draft ? (
          <>
            <ChannelSettingsDisclosure
              title="QQ 私聊能力与表情收藏"
              description="联网、语音输入、语音回复与账号收藏"
              open
            >
              <SettingsToggle
                label="允许 QQ 主人私聊联网查资料"
                description={`只允许公网搜索和读取；群聊及微信不获得工具权限。当前服务：${snapshot?.search_provider} / ${snapshot?.reader_provider}。`}
                checked={draft.qq_owner_public_web_enabled ?? false}
                disabled={disabled}
                onChange={(v) => toggle("qq_owner_public_web_enabled", v)}
              />
              <SettingsToggle
                label="允许 QQ 主人私聊发现 Agent 能力"
                description="显示当前已安装的文件、文档、日历、任务与插件能力。写入和外部操作继续经过权限检查。"
                checked={draft.qq_owner_agent_enabled ?? false}
                disabled={disabled}
                onChange={(v) => toggle("qq_owner_agent_enabled", v)}
              />
              <SettingsToggle
                label="允许 QQ 私聊语音回复"
                description="允许角色按当前语义选择语音；关闭后私聊只回复文字，不改变各群按需语音设置。"
                checked={draft.qq_owner_voice_reply_enabled ?? true}
                disabled={disabled}
                onChange={(v) => toggle("qq_owner_voice_reply_enabled", v)}
              />
              <SettingsToggle
                label="识别 QQ 私聊语音消息"
                description={`仅配对主人可使用。语音识别服务：${snapshot?.stt_provider === "disabled" ? "未配置，请先在后端配置语音识别服务" : snapshot?.stt_provider}；群语音输入暂不支持。`}
                checked={draft.qq_owner_voice_input_enabled ?? true}
                disabled={disabled}
                onChange={(v) => toggle("qq_owner_voice_input_enabled", v)}
              />
              <SettingsToggle
                label="将学到的 QQ 表情同步到原生收藏"
                description="还需开启对应私聊或群的表情学习，以及 QQ 连接的表情开关。关闭后不再新增账号收藏，已有收藏由 QQ 管理。"
                checked={draft.qq_native_favorites_enabled ?? true}
                disabled={disabled}
                onChange={(v) => toggle("qq_native_favorites_enabled", v)}
              />
            </ChannelSettingsDisclosure>
            <ChannelSettingsDisclosure
              title="群聊旁听与按需压缩"
              description="最近原文、保留时间、每人配额和模型预算"
            >
              <SettingsToggle
                label="收集已授权群的讨论上下文"
                description="普通文字只进入短期缓存；仅授权成员 @ 才触发回复。成员权限、私聊记忆和长期记忆规则独立。"
                checked={draft.group_discussion?.enabled ?? true}
                disabled={disabled}
                onChange={(v) =>
                  setDraft({
                    ...draft,
                    group_discussion: { ...draft.group_discussion, enabled: v },
                  })
                }
              />
              <p>
                保存旁听设置会清空旧短期缓存。重连、群停用或受众变化也会失效；摘要失败时使用预算内的最近原文。
              </p>
              {limits.map((group) => (
                <fieldset
                  className="channel-settings-fields"
                  disabled={disabled}
                  key={group.title}
                >
                  <legend>{group.title}</legend>
                  {group.fields.map((field) => (
                    <label key={field.key}>
                      {field.label}
                      <input
                        aria-label={field.label}
                        type="number"
                        min={field.min}
                        max={field.max}
                        step={field.step ?? 1}
                        value={numbers[field.key] ?? ""}
                        onChange={(event) =>
                          setNumbers({
                            ...numbers,
                            [field.key]: event.currentTarget.value,
                          })
                        }
                      />
                      <small>
                        {field.min}–{field.max}
                      </small>
                    </label>
                  ))}
                </fieldset>
              ))}
              <p>
                实际每人容量还受“群容量 ÷
                受众人数”约束；旁听输入最多占当前模型可用输入的四分之一。参考
                token 不是供应商精确计数，增大此值不保证更好的回答。
              </p>
            </ChannelSettingsDisclosure>
            {!policy ? (
              <p role="alert">请填写范围内的数值；每人缓存不能大于整群缓存。</p>
            ) : null}
          </>
        ) : (
          <p role="status">
            {runtimeOnline
              ? "正在读取渠道权限…"
              : "Runtime 离线，连接后可读取设置。"}
          </p>
        )}
        <div className="qq-channel-actions">
          <button
            type="button"
            className="channels-settings-primary-action"
            disabled={disabled || !policy || !dirty}
            onClick={() => void save()}
          >
            {busy ? "正在保存…" : "保存渠道权限与预算"}
          </button>
          <button
            type="button"
            className="qq-channel-secondary-action"
            disabled={!runtimeOnline || busy}
            onClick={() => {
              setFresh(false);
              setNotice(null);
              setReadRevision((v) => v + 1);
            }}
          >
            刷新渠道权限
          </button>
        </div>
        {notice ? <p role="status">{notice}</p> : null}
      </div>
    </section>
  );
}
