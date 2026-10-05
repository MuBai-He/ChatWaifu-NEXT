import { ChannelsSettingsSection } from "./ChannelsSettingsSection";
import { useSettingsRuntime } from "../settings/useSettingsRuntime";
import {
  useConnectionControls,
  useScopeControls,
} from "../connection/clientControls";

export function ChannelsSettingsPage() {
  const runtime = useSettingsRuntime();
  const connection = useConnectionControls();
  const scope = useScopeControls();
  return (
    <main className="channel-settings-page">
      <header className="channel-settings-page-header">
        <a href="/">返回对话</a>
        <h1>消息渠道设置</h1>
        <span>
          {runtime.connection === "connected"
            ? "Runtime 已连接"
            : runtime.connection === "offline"
              ? "Runtime 离线"
              : "正在连接 Runtime"}
        </span>
        <div className="qq-channel-actions">
          <button
            type="button"
            className="qq-channel-secondary-action"
            disabled={!connection}
            onClick={() => connection?.open()}
          >
            连接设置
          </button>
          <button
            type="button"
            className="qq-channel-secondary-action"
            disabled={!scope}
            onClick={() => scope?.open()}
          >
            管理参与者
          </button>
        </div>
        {runtime.error ? <p role="status">{runtime.error}</p> : null}
      </header>
      <ChannelsSettingsSection
        context={{
          runtime: { connection: runtime.connection },
          appearance: { character: runtime.character },
        }}
      />
    </main>
  );
}
