import { MemoryControlCenter } from "../chat/MemoryControlCenter";
import { SkillsControlCenter } from "../chat/SkillsControlCenter";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { SettingsIcon } from "./SettingsIcon";
import { SettingsGroup } from "./SettingsPrimitives";

export function DataSettingsSection({
  context,
}: {
  context: DesktopSettingsContext;
}) {
  const { data } = context;
  return (
    <>
      <div className="desktop-settings-tool-grid">
        <article className="desktop-settings-tool-card">
          <span>
            <SettingsIcon name="memory" />
          </span>
          <h2>结构化记忆</h2>
          <p>查看建议、修正事实、确认敏感内容并管理遗忘。</p>
          <MemoryControlCenter
            sessionId={data.sessionId}
            onChanged={data.refreshMemories}
          />
        </article>
        <article className="desktop-settings-tool-card">
          <span>
            <SettingsIcon name="skills" />
          </span>
          <h2>Skills 与插件</h2>
          <p>管理能力权限、插件隔离、确认请求和最近运行。</p>
          <SkillsControlCenter sessionId={data.sessionId} />
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
            disabled={!data.sessionId || data.resetting}
            onClick={() => void context.resetConversationAndMemory()}
          >
            {data.resetting ? "正在重置…" : "全部重置"}
          </button>
        </div>
      </SettingsGroup>
    </>
  );
}
