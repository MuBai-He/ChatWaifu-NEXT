import { AppearanceSettingsSection } from "./AppearanceSettingsSection";
import { ChannelsSettingsSection } from "./ChannelsSettingsSection";
import { CompanionSettingsPanel } from "./CompanionSettingsPanel";
import { DataSettingsSection } from "./DataSettingsSection";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { ModelsSettingsSection } from "./ModelsSettingsSection";
import { defineSettingsRegistry } from "./settingsRegistry";
import { VoiceSettingsSection } from "./VoiceSettingsSection";

export const desktopSettingsRegistry =
  defineSettingsRegistry<DesktopSettingsContext>()([
    {
      id: "appearance",
      label: "桌宠",
      description: "窗口与显示",
      icon: "pet",
      component: AppearanceSettingsSection,
    },
    {
      id: "companion",
      label: "陪伴",
      description: "唤醒、主动与休眠",
      icon: "companion",
      component: CompanionSettingsPanel,
    },
    {
      id: "voice",
      label: "声音",
      description: "角色语音",
      icon: "voice",
      component: VoiceSettingsSection,
    },
    {
      id: "models",
      label: "模型",
      description: "AI 与记忆路由",
      icon: "models",
      component: ModelsSettingsSection,
    },
    {
      id: "channels",
      label: "渠道",
      description: "微信与外部消息",
      icon: "channels",
      component: ChannelsSettingsSection,
    },
    {
      id: "data",
      label: "数据",
      description: "记忆与扩展",
      icon: "data",
      component: DataSettingsSection,
    },
  ]);

export type DesktopSettingsSectionId =
  (typeof desktopSettingsRegistry)[number]["id"];
