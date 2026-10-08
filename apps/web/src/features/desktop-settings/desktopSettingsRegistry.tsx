import {
  MemorySettingsSection,
  ExtensionsSettingsSection,
} from "../settings/CommonSettingsSections";
import { ConnectionSettingsSection } from "./ConnectionSettingsSection";
import { AgentSettingsSection } from "../agent-settings/AgentSettingsSection";
import { AppearanceSettingsSection } from "./AppearanceSettingsSection";
import { ChannelsSettingsSection } from "../channels-settings/ChannelsSettingsSection";
import { CompanionSettingsPanel } from "./CompanionSettingsPanel";
import { DataSettingsSection } from "./DataSettingsSection";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { ModelsSettingsSection } from "./ModelsSettingsSection";
import { defineSettingsRegistry } from "./settingsRegistry";
import { VoiceSettingsSection } from "./VoiceSettingsSection";
import { PersonalAssistantSettingsSection } from "./PersonalAssistantSettingsSection";

export const desktopSettingsRegistry =
  defineSettingsRegistry<DesktopSettingsContext>()([
    {
      id: "connection",
      group: "连接与设备",
      label: "连接与诊断",
      description: "运行方式与对话范围",
      icon: "models",
      component: ConnectionSettingsSection,
    },
    {
      id: "appearance",
      group: "聊天",
      label: "桌宠",
      description: "窗口与显示",
      icon: "pet",
      component: AppearanceSettingsSection,
    },
    {
      id: "companion",
      group: "聊天",
      label: "陪伴",
      description: "唤醒、主动与休眠",
      icon: "companion",
      component: CompanionSettingsPanel,
    },
    {
      id: "voice",
      group: "模型与声音",
      label: "声音",
      description: "角色语音",
      icon: "voice",
      component: VoiceSettingsSection,
    },
    {
      id: "models",
      group: "模型与声音",
      label: "模型",
      description: "聊天、行为决策与记忆路由",
      keywords: ["Jev", "TypeSafe", "API Key", "预算"],
      icon: "models",
      component: ModelsSettingsSection,
    },
    {
      id: "channels",
      group: "聊天",
      label: "聊天与渠道",
      description: "QQ、微信、自由交流与回复节奏",
      keywords: ["打字", "延迟", "语音", "戳一戳", "群聊", "表情", "照片"],
      icon: "channels",
      component: ChannelsSettingsSection,
    },
    {
      id: "data",
      group: "连接与设备",
      label: "本地数据与模型包",
      description: "数据清理与 Worker Pack",
      icon: "data",
      component: DataSettingsSection,
    },
    {
      id: "agent",
      group: "能力与任务",
      label: "能力与任务",
      description: "工具权限、后台任务与文件",
      icon: "data",
      component: AgentSettingsSection,
    },
    {
      id: "personal-assistant",
      group: "日程",
      label: "日程与提醒",
      description: "日历、提醒与任务",
      icon: "companion",
      component: PersonalAssistantSettingsSection,
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
      id: "extensions",
      label: "扩展与 MCP",
      description: "Skills、插件和服务连接",
      icon: "plugin",
      group: "能力与任务",
      component: ExtensionsSettingsSection,
    },
  ]);

export type DesktopSettingsSectionId =
  (typeof desktopSettingsRegistry)[number]["id"];
