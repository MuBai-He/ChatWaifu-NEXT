import { useState, useSyncExternalStore } from "react";
import { AgentSettingsSection } from "../agent-settings/AgentSettingsSection";
import { ChannelsSettingsSection } from "../channels-settings/ChannelsSettingsSection";
import {
  getRuntimeContextRevision,
  subscribeRuntimeContext,
} from "../chat/runtimeEndpoint";
import { ModelsSettingsSection } from "./ModelsSettingsSection";
import { VoiceSettingsSection } from "./VoiceSettingsSection";
import { SettingsShell } from "./SettingsShell";
import {
  settingsContext,
  type CommonSettingsContext,
} from "./SettingsRuntimeContext";
import { defineSettingsRegistry } from "./settingsRegistry";
import { useSettingsRuntime } from "./useSettingsRuntime";
import {
  ConnectionSettingsSection,
  ExtensionsSettingsSection,
  MemorySettingsSection,
  ScheduleSettingsSection,
} from "./CommonSettingsSections";

const sections = defineSettingsRegistry<CommonSettingsContext>()([
  {
    id: "channels",
    label: "聊天与渠道",
    description: "QQ、微信、自由交流与回复节奏",
    icon: "channels",
    group: "聊天",
    keywords: [
      "延迟",
      "打字",
      "戳一戳",
      "群聊",
      "私聊",
      "语音",
      "权限",
      "表情",
      "照片",
    ],
    component: ChannelsSettingsSection,
  },
  {
    id: "models",
    label: "模型",
    description: "聊天、行为决策与记忆路由",
    icon: "models",
    group: "模型与声音",
    keywords: ["Jev", "TypeSafe", "API Key", "预算", "Embedding"],
    component: ModelsSettingsSection,
  },
  {
    id: "voice",
    label: "声音",
    description: "角色语音与实时对话",
    icon: "voice",
    group: "模型与声音",
    keywords: ["TTS", "Realtime", "麦克风"],
    component: VoiceSettingsSection,
  },
  {
    id: "agent",
    label: "能力与任务",
    description: "工具授权、后台任务与文件",
    icon: "skills",
    group: "能力与任务",
    keywords: ["Agent", "开发", "审批"],
    component: AgentSettingsSection,
  },
  {
    id: "extensions",
    label: "扩展与 MCP",
    description: "Skills、插件和服务连接",
    icon: "plugin",
    group: "能力与任务",
    component: ExtensionsSettingsSection,
  },
  {
    id: "memory",
    label: "记忆与资料",
    description: "记忆建议、事实与来源",
    icon: "memory",
    group: "记忆与资料",
    component: MemorySettingsSection,
  },
  {
    id: "personal-assistant",
    label: "日程与提醒",
    description: "日历、提醒和计划任务",
    icon: "companion",
    group: "日程",
    component: ScheduleSettingsSection,
  },
  {
    id: "connection",
    label: "连接与诊断",
    description: "Runtime、参与者与运行记录",
    icon: "models",
    group: "连接与设备",
    component: ConnectionSettingsSection,
  },
]);

export function WebSettingsPage({
  initialSection = "channels",
}: {
  initialSection?: string;
}) {
  const epoch = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  return <WebSettingsContent key={epoch} initialSection={initialSection} />;
}

function WebSettingsContent({ initialSection }: { initialSection: string }) {
  const runtime = useSettingsRuntime();
  const context = settingsContext(runtime);
  const validInitial = sections.some((section) => section.id === initialSection)
    ? initialSection
    : "channels";
  const [selectedId, setSelectedId] = useState(validInitial);
  const [visited, setVisited] = useState(() => new Set([validInitial]));
  const select = (id: string) => {
    setSelectedId(id);
    setVisited((current) => new Set([...current, id]));
    const url = new URL(window.location.href);
    url.pathname = `/settings/${id}`;
    window.history.replaceState(null, "", url);
  };
  return (
    <SettingsShell
      sections={sections}
      selectedId={selectedId}
      onSelect={select}
      context={context}
      connection={runtime.connection}
      subtitle="设置中心"
      footer={
        <a className="settings-return" href="/">
          返回对话 ↗
        </a>
      }
    >
      {sections
        .filter((section) => visited.has(section.id))
        .map((section) => {
          const Component = section.component;
          return (
            <div key={section.id} hidden={section.id !== selectedId}>
              <Component context={context} />
            </div>
          );
        })}
      {runtime.error && (
        <p role="alert" className="settings-error">
          {runtime.error}
          {runtime.connection === "offline" && (
            <button onClick={runtime.reconnect}>立即重连</button>
          )}
        </p>
      )}
    </SettingsShell>
  );
}
