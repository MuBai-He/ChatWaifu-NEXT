import {
  buildTtsProviderChoices,
  providerSelectorValue,
  readTtsProviderPreferences,
  resolveProviderSelection,
  saveTtsProviderPreference,
} from "../chat/ttsProviderPresentation";
import { TtsConfigurationPanel } from "./TtsConfigurationPanel";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { SettingsGroup, SettingsSectionIntro } from "./SettingsPrimitives";

export function VoiceSettingsSection({
  context,
}: {
  context: DesktopSettingsContext;
}) {
  const { voice } = context;
  const preferences = readTtsProviderPreferences(voice.ttsProviders);
  const choices = buildTtsProviderChoices(
    voice.ttsProviders,
    voice.ttsProviderId,
    preferences,
  );
  const selected = choices.find(
    (provider) =>
      provider.id ===
      providerSelectorValue(voice.ttsProviders, voice.ttsProviderId),
  );

  const changeProvider = async (selectionId: string) => {
    const nextProviderId = resolveProviderSelection(
      selectionId,
      voice.ttsProviders,
      voice.ttsProviderId,
      preferences,
    );
    saveTtsProviderPreference(voice.ttsProviders, nextProviderId);
    await voice.changeTtsProvider(nextProviderId);
  };
  const changeConfiguredProvider = async (next: string) => {
    saveTtsProviderPreference(voice.ttsProviders, next);
    if (voice.ttsProviders.some((provider) => provider.provider_id === next))
      await voice.changeTtsProvider(next);
  };

  return (
    <>
      <section className="desktop-settings-voice-card">
        <SettingsSectionIntro
          icon="voice"
          title="角色声音"
          description="选择桌宠回答时使用的本地或云端实时语音。"
        />
        <label className="desktop-settings-select-row">
          <div>
            <strong>当前语音</strong>
            <small>
              {selected
                ? `${selected.variantLabel ? `${selected.variantLabel} · ` : ""}${selected.model}`
                : "正在读取 Runtime 配置"}
            </small>
          </div>
          <select
            value={providerSelectorValue(
              voice.ttsProviders,
              voice.ttsProviderId,
            )}
            disabled={!voice.sessionId || voice.ttsSwitching}
            onChange={(event) => void changeProvider(event.target.value)}
            aria-label="选择桌宠语音"
          >
            {choices.length ? (
              choices.map((provider) => (
                <option
                  value={provider.id}
                  key={provider.id}
                  disabled={provider.status === "unavailable"}
                >
                  {provider.displayName}
                </option>
              ))
            ) : (
              <option
                value={providerSelectorValue(
                  voice.ttsProviders,
                  voice.ttsProviderId,
                )}
              >
                正在读取…
              </option>
            )}
          </select>
        </label>
      </section>

      <SettingsGroup title="可用语音" description="模型只在需要时加载">
        {choices.length ? (
          choices.map((provider) => (
            <div className="desktop-settings-provider" key={provider.id}>
              <i className={provider.status} />
              <div>
                <strong>{provider.displayName}</strong>
                <small>
                  {provider.variantLabel ? `${provider.variantLabel} · ` : ""}
                  {provider.model} · {provider.languages.join(" / ")}
                </small>
              </div>
              <span>{provider.modelLoaded ? "已加载" : provider.status}</span>
            </div>
          ))
        ) : (
          <p className="desktop-settings-empty">等待 Runtime 返回语音能力…</p>
        )}
      </SettingsGroup>

      <TtsConfigurationPanel
        preferredProviderId={voice.ttsProviderId}
        onProviderIdChange={changeConfiguredProvider}
        onSaved={voice.refreshTtsProviders}
      />

      <p className="desktop-settings-info">
        麦克风采集和声音播放只由桌宠窗口负责，设置页不会建立第二条媒体链路，因此不会产生重叠语音。
      </p>
    </>
  );
}
