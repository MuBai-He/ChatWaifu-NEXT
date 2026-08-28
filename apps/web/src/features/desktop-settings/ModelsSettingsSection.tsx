import { ModelSettingsPanel } from "../chat/ModelSettingsPanel";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { SettingsSectionIntro } from "./SettingsPrimitives";

export function ModelsSettingsSection({
  context,
}: {
  context: DesktopSettingsContext;
}) {
  return (
    <section className="desktop-settings-models" aria-label="模型设置">
      <SettingsSectionIntro
        icon="models"
        title="模型路由"
        description="聊天、记忆提取、总结和向量模型可以分别配置。"
      />
      <ModelSettingsPanel sessionId={context.sessionId} />
    </section>
  );
}
