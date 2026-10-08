import { ModelSettingsPanel } from "../chat/ModelSettingsPanel";
import type { CommonSettingsContext } from "./SettingsRuntimeContext";
import { SettingsSectionIntro } from "./SettingsPrimitives";

export function ModelsSettingsSection({
  context,
}: {
  context: CommonSettingsContext;
}) {
  return (
    <section className="desktop-settings-models" aria-label="模型设置">
      <SettingsSectionIntro
        icon="models"
        title="模型路由"
        description="分别选择正式回复、行为决策、记忆和向量检索使用的模型。"
      />
      <ModelSettingsPanel sessionId={context.sessionId} compact />
    </section>
  );
}
