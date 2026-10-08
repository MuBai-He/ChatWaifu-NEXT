import { useState } from "react";
import { MemoryControlCenter } from "../chat/MemoryControlCenter";
import { SkillsControlCenter } from "../chat/SkillsControlCenter";
import { InteractionDiagnosticsPanel } from "../diagnostics/InteractionDiagnosticsPanel";
import { AgendaOverview } from "../personal-assistant/AgendaOverview";
import { OrganizerPanel } from "../personal-assistant/OrganizerPanel";
import { PersonalCalendarPanel } from "../personal-assistant/PersonalCalendarPanel";
import {
  useConnectionControls,
  useScopeControls,
} from "../connection/clientControls";
import { McpConnectionsPanel } from "./McpConnectionsPanel";
import { SettingsGroup, SettingsSectionIntro } from "./SettingsPrimitives";
import type { CommonSettingsContext } from "./SettingsRuntimeContext";

type Props = { context: CommonSettingsContext };

export function MemorySettingsSection({ context }: Props) {
  return (
    <>
      <SettingsSectionIntro
        icon="memory"
        title="记忆与资料"
        description="查看记忆建议、修正事实和管理遗忘。照片与表情在对应消息渠道中管理。"
      />
      <SettingsGroup
        title="结构化记忆"
        description="每条记忆都有来源，敏感内容需要确认。"
      >
        <MemoryControlCenter
          sessionId={context.sessionId}
          onChanged={context.data.refreshMemories}
        />
      </SettingsGroup>
    </>
  );
}

export function ExtensionsSettingsSection({ context }: Props) {
  return (
    <>
      <SettingsSectionIntro
        icon="plugin"
        title="扩展与连接"
        description="管理已安装能力、插件和 MCP 服务。工具的使用授权在“能力与任务”中查看。"
      />
      <SettingsGroup
        title="Skills 与插件"
        description="查看运行记录、插件状态和确认请求。"
      >
        <SkillsControlCenter sessionId={context.sessionId} />
      </SettingsGroup>
      <SettingsGroup
        title="MCP 服务"
        description="连接服务并检查它提供的工具、资源和提示词。"
      >
        <McpConnectionsPanel sessionId={context.sessionId} />
      </SettingsGroup>
    </>
  );
}

export function ScheduleSettingsSection({ context }: Props) {
  const [notice, setNotice] = useState("");
  return (
    <div className="personal-assistant-settings">
      <SettingsSectionIntro
        icon="companion"
        title="日程与提醒"
        description="统一查看日历、提醒和计划任务；设备上的执行状态也会显示在这里。"
      />
      {context.sessionId ? (
        <>
          <AgendaOverview sessionId={context.sessionId} />
          <OrganizerPanel sessionId={context.sessionId} />
          <SettingsGroup
            title="Google 日历与任务"
            description="管理当前 Runtime 已连接的账号；首次授权和权限升级请从桌面设置完成。"
          >
            <PersonalCalendarPanel
              sessionId={context.sessionId}
              onUpgrade={() =>
                setNotice("请在桌面应用的“日程与提醒”中升级 Google 账号权限。")
              }
            />
            {notice && <p role="status">{notice}</p>}
          </SettingsGroup>
        </>
      ) : (
        <p role="status">连接后可查看当前参与者的日程。</p>
      )}
    </div>
  );
}

export function ConnectionSettingsSection({ context }: Props) {
  const connection = useConnectionControls();
  const scope = useScopeControls();
  return (
    <>
      <SettingsSectionIntro
        icon="models"
        title="连接与诊断"
        description="确认配置保存到哪个 Runtime，管理参与者，并检查模型与消息的运行记录。"
      />
      <SettingsGroup
        title="当前连接"
        description="切换 Runtime 或参与者后，设置会重新读取对应范围。"
      >
        <div className="settings-action-row">
          <div>
            <strong>
              {connection?.mode === "remote" ? "远程 Runtime" : "当前 Runtime"}
            </strong>
            <small>
              {context.runtime.health
                ? `版本 ${context.runtime.health.version}`
                : "等待连接"}
            </small>
          </div>
          <button
            type="button"
            disabled={!connection}
            onClick={() => connection?.open()}
          >
            连接设置
          </button>
        </div>
        <div className="settings-action-row">
          <div>
            <strong>对话参与者</strong>
            <small>管理自己的名称、角色和当前对话范围。</small>
          </div>
          <button
            type="button"
            disabled={!scope}
            onClick={(event) => scope?.open(event.currentTarget)}
          >
            管理参与者
          </button>
        </div>
      </SettingsGroup>
      <InteractionDiagnosticsPanel
        sessionId={context.sessionId}
        runtimeOnline={context.runtime.connection === "connected"}
      />
    </>
  );
}
