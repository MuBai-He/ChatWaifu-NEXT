import { useState } from "react";
import { MemoryControlCenter } from "../chat/MemoryControlCenter";
import { ModelSettingsPanel } from "../chat/ModelSettingsPanel";
import { SkillsControlCenter } from "../chat/SkillsControlCenter";
import { useChatSession } from "../chat/useChatSession";
import { useDesktopPreferences } from "../desktop-pet/useDesktopPreferences";

type SettingsSection = "appearance" | "voice" | "models" | "data";

const sections: Array<{
  id: SettingsSection;
  label: string;
  description: string;
  icon: string;
}> = [
  { id: "appearance", label: "桌宠", description: "窗口与显示", icon: "宠" },
  { id: "voice", label: "声音", description: "角色语音", icon: "声" },
  { id: "models", label: "模型", description: "AI 与记忆路由", icon: "模" },
  { id: "data", label: "数据", description: "记忆与扩展", icon: "据" },
];

export function DesktopSettingsPage() {
  const {
    canvasRef,
    snapshot,
    rendererKind,
    health,
    character,
    sessionId,
    connection,
    error: runtimeError,
    resetting,
    ttsProviders,
    ttsProviderId,
    ttsSwitching,
    changeTtsProvider,
    resetAll,
    refreshMemories,
  } = useChatSession({ playbackEnabled: false });
  const {
    preferences,
    loading,
    saving,
    error: preferenceError,
    desktopHost,
    setDisplay,
    setAlwaysOnTop,
    setClickThrough,
    setOverlayVisible,
  } = useDesktopPreferences();
  const [section, setSection] = useState<SettingsSection>("appearance");
  const selected = sections.find((item) => item.id === section) ?? sections[0];

  const reset = async () => {
    if (
      !window.confirm(
        "确定清空当前对话、全部明确记忆和本地生成语音吗？此操作无法撤销。",
      )
    )
      return;
    await resetAll();
  };

  return (
    <main className="desktop-settings-page">
      <aside className="desktop-settings-sidebar">
        <header>
          <span className="desktop-settings-app-icon">宁</span>
          <div>
            <strong>ChatWaifu NEXT</strong>
            <small>桌宠设置</small>
          </div>
        </header>

        <nav aria-label="设置分类">
          {sections.map((item) => (
            <button
              className={section === item.id ? "active" : ""}
              type="button"
              key={item.id}
              onClick={() => setSection(item.id)}
              aria-current={section === item.id ? "page" : undefined}
            >
              <span>{item.icon}</span>
              <div>
                <strong>{item.label}</strong>
                <small>{item.description}</small>
              </div>
            </button>
          ))}
        </nav>

        <footer>
          <i className={connection} />
          <div>
            <strong>{connectionLabel(connection)}</strong>
            <small>
              {health?.version ? `Runtime ${health.version}` : "本地服务"}
            </small>
          </div>
        </footer>
      </aside>

      <section className="desktop-settings-content">
        <header className="desktop-settings-heading">
          <div>
            <small>{selected.description}</small>
            <h1>{selected.label}</h1>
          </div>
          <span className={`desktop-settings-runtime ${connection}`}>
            <i />
            {connection === "connected"
              ? "运行正常"
              : connectionLabel(connection)}
          </span>
        </header>

        <div className="desktop-settings-scroll">
          {section === "appearance" ? (
            <AppearanceSettings
              canvasRef={canvasRef}
              characterName={character?.display_name ?? "绫地宁宁"}
              rendererKind={rendererKind}
              avatarStatus={snapshot?.status ?? "loading"}
              preferences={preferences}
              disabled={loading || saving}
              desktopHost={desktopHost}
              setOverlayVisible={setOverlayVisible}
              setAlwaysOnTop={setAlwaysOnTop}
              setClickThrough={setClickThrough}
              setDisplay={setDisplay}
            />
          ) : null}

          {section === "voice" ? (
            <VoiceSettings
              providers={ttsProviders}
              providerId={ttsProviderId}
              switching={ttsSwitching}
              sessionReady={Boolean(sessionId)}
              onChange={changeTtsProvider}
            />
          ) : null}

          {section === "models" ? (
            <section className="desktop-settings-models" aria-label="模型设置">
              <div className="desktop-settings-section-intro">
                <span>AI</span>
                <div>
                  <h2>模型路由</h2>
                  <p>聊天、记忆提取、总结和向量模型可以分别配置。</p>
                </div>
              </div>
              <ModelSettingsPanel sessionId={sessionId} />
            </section>
          ) : null}

          {section === "data" ? (
            <DataSettings
              sessionId={sessionId}
              resetting={resetting}
              onMemoryChanged={refreshMemories}
              onReset={reset}
            />
          ) : null}

          {runtimeError || preferenceError ? (
            <p className="desktop-settings-error" role="alert">
              {preferenceError ?? runtimeError}
            </p>
          ) : null}
        </div>
      </section>
    </main>
  );
}

type AppearanceSettingsProps = {
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  characterName: string;
  rendererKind: "live2d" | "fake";
  avatarStatus: string;
  preferences: ReturnType<typeof useDesktopPreferences>["preferences"];
  disabled: boolean;
  desktopHost: boolean;
  setOverlayVisible: (enabled: boolean) => Promise<void>;
  setAlwaysOnTop: (enabled: boolean) => Promise<void>;
  setClickThrough: (enabled: boolean) => Promise<void>;
  setDisplay: (display: {
    showSubtitles?: boolean;
    showStatus?: boolean;
  }) => Promise<void>;
};

function AppearanceSettings({
  canvasRef,
  characterName,
  rendererKind,
  avatarStatus,
  preferences,
  disabled,
  desktopHost,
  setOverlayVisible,
  setAlwaysOnTop,
  setClickThrough,
  setDisplay,
}: AppearanceSettingsProps) {
  return (
    <>
      <section className="desktop-settings-preview-card">
        <div className="desktop-settings-avatar-preview">
          <canvas key={rendererKind} ref={canvasRef} />
        </div>
        <div>
          <small>CURRENT CHARACTER</small>
          <h2>{characterName}</h2>
          <p>
            {rendererKind === "live2d" ? "Live2D" : "安全回退"} · {avatarStatus}
          </p>
          <span>拖动桌宠窗口边缘即可调整大小，位置和尺寸会自动保存。</span>
        </div>
      </section>

      {!desktopHost ? (
        <p className="desktop-settings-preview-note">
          当前是浏览器预览；窗口置顶、显示和鼠标穿透会在桌面版中生效。
        </p>
      ) : null}

      <SettingsGroup title="窗口" description="控制桌宠在桌面上的行为">
        <SettingsToggle
          label="显示桌宠"
          description="隐藏后仍可从托盘或这里重新显示"
          checked={preferences.overlayVisible}
          disabled={disabled}
          onChange={setOverlayVisible}
        />
        <SettingsToggle
          label="始终置顶"
          description="让宁宁保持在其他窗口上方"
          checked={preferences.alwaysOnTop}
          disabled={disabled}
          onChange={setAlwaysOnTop}
        />
        <SettingsToggle
          label="鼠标穿透"
          description="开启后点击会落到下方窗口，可在本设置页或托盘关闭"
          checked={preferences.clickThrough}
          disabled={disabled}
          onChange={setClickThrough}
        />
      </SettingsGroup>

      <SettingsGroup title="画面" description="只改变显示，不影响语音和动作">
        <SettingsToggle
          label="显示字幕"
          description="显示宁宁当前正在说的话"
          checked={preferences.showSubtitles}
          disabled={disabled}
          onChange={(enabled) => setDisplay({ showSubtitles: enabled })}
        />
        <SettingsToggle
          label="显示在线状态"
          description="同时控制 NENE ONLINE 与两侧装饰线"
          checked={preferences.showStatus}
          disabled={disabled}
          onChange={(enabled) => setDisplay({ showStatus: enabled })}
        />
      </SettingsGroup>
    </>
  );
}

type VoiceSettingsProps = {
  providers: ReturnType<typeof useChatSession>["ttsProviders"];
  providerId: string;
  switching: boolean;
  sessionReady: boolean;
  onChange: (providerId: string) => Promise<void>;
};

function VoiceSettings({
  providers,
  providerId,
  switching,
  sessionReady,
  onChange,
}: VoiceSettingsProps) {
  const selected = providers.find(
    (provider) => provider.provider_id === providerId,
  );
  return (
    <>
      <section className="desktop-settings-voice-card">
        <div className="desktop-settings-section-intro">
          <span>声</span>
          <div>
            <h2>角色声音</h2>
            <p>选择桌宠回答时使用的本地语音模型。</p>
          </div>
        </div>
        <label className="desktop-settings-select-row">
          <div>
            <strong>当前语音</strong>
            <small>{selected?.model ?? "正在读取 Runtime 配置"}</small>
          </div>
          <select
            value={providerId}
            disabled={!sessionReady || switching}
            onChange={(event) => void onChange(event.target.value)}
            aria-label="选择桌宠语音"
          >
            {providers.length ? (
              providers.map((provider) => (
                <option
                  value={provider.provider_id}
                  key={provider.provider_id}
                  disabled={provider.status === "unavailable"}
                >
                  {provider.display_name}
                </option>
              ))
            ) : (
              <option value={providerId}>正在读取…</option>
            )}
          </select>
        </label>
      </section>

      <SettingsGroup title="可用语音" description="模型只在需要时加载">
        {providers.length ? (
          providers.map((provider) => (
            <div
              className="desktop-settings-provider"
              key={provider.provider_id}
            >
              <i className={provider.status} />
              <div>
                <strong>{provider.display_name}</strong>
                <small>
                  {provider.model} · {provider.languages.join(" / ")}
                </small>
              </div>
              <span>{provider.model_loaded ? "已加载" : provider.status}</span>
            </div>
          ))
        ) : (
          <p className="desktop-settings-empty">等待 Runtime 返回语音能力…</p>
        )}
      </SettingsGroup>

      <p className="desktop-settings-info">
        麦克风采集和声音播放只由桌宠窗口负责，设置页不会建立第二条媒体链路，因此不会产生重叠语音。
      </p>
    </>
  );
}

function DataSettings({
  sessionId,
  resetting,
  onMemoryChanged,
  onReset,
}: {
  sessionId: string | null;
  resetting: boolean;
  onMemoryChanged: () => Promise<void>;
  onReset: () => Promise<void>;
}) {
  return (
    <>
      <div className="desktop-settings-tool-grid">
        <article className="desktop-settings-tool-card">
          <span>忆</span>
          <h2>结构化记忆</h2>
          <p>查看建议、修正事实、确认敏感内容并管理遗忘。</p>
          <MemoryControlCenter
            sessionId={sessionId}
            onChanged={onMemoryChanged}
          />
        </article>
        <article className="desktop-settings-tool-card">
          <span>技</span>
          <h2>Skills 与插件</h2>
          <p>管理能力权限、插件隔离、确认请求和最近运行。</p>
          <SkillsControlCenter sessionId={sessionId} />
        </article>
      </div>

      <SettingsGroup title="本地数据" description="数据只保存在这台设备">
        <div className="desktop-settings-danger-row">
          <div>
            <strong>重置对话与记忆</strong>
            <small>清空当前对话、明确记忆和已生成语音，操作无法撤销。</small>
          </div>
          <button
            type="button"
            disabled={!sessionId || resetting}
            onClick={() => void onReset()}
          >
            {resetting ? "正在重置…" : "全部重置"}
          </button>
        </div>
      </SettingsGroup>
    </>
  );
}

function SettingsGroup({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="desktop-settings-group">
      <header>
        <h2>{title}</h2>
        <p>{description}</p>
      </header>
      <div>{children}</div>
    </section>
  );
}

function SettingsToggle({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (enabled: boolean) => Promise<void>;
}) {
  return (
    <label className="desktop-settings-toggle-row">
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => void onChange(event.currentTarget.checked)}
        aria-label={label}
      />
    </label>
  );
}

function connectionLabel(
  connection: "connecting" | "connected" | "offline",
): string {
  if (connection === "connected") return "已连接";
  if (connection === "connecting") return "正在连接";
  return "Runtime 离线";
}
