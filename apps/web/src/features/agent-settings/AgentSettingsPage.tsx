import { AgentSettingsSection } from "./AgentSettingsSection";
import { useSettingsRuntime } from "../settings/useSettingsRuntime";

export function AgentSettingsPage() {
  const runtime = useSettingsRuntime();
  return (
    <main className="channel-settings-page settings-controls">
      <header className="channel-settings-page-header">
        <a href="/">返回对话</a>
        <h1>能力与任务</h1>
        <a href="/settings/channels">消息渠道设置</a>
        {runtime.error && <p role="status">{runtime.error}</p>}
      </header>
      <AgentSettingsSection context={{ sessionId: runtime.sessionId }} />
    </main>
  );
}
