import type {
  AliyunCloudTtsProviderId,
  TtsProviderSnapshot,
} from "./types";

export const ALIYUN_BAILIAN_ENTRY_ID = "aliyun_bailian";
export const ALIYUN_TTS_PREFERENCE_KEY =
  "chatwaifu.next.aliyun_tts_provider_id";

const ALIYUN_PROVIDER_IDS: AliyunCloudTtsProviderId[] = [
  "aliyun_cosyvoice_realtime",
  "aliyun_qwen_realtime",
];

export interface TtsProviderChoice {
  id: string;
  displayName: string;
  model: string;
  languages: string[];
  status: TtsProviderSnapshot["status"];
  modelLoaded: boolean;
  selected: boolean;
  actualProviderId: string;
  engineLabel?: string;
}

export function isAliyunCloudTtsProviderId(
  providerId: string,
): providerId is AliyunCloudTtsProviderId {
  return ALIYUN_PROVIDER_IDS.includes(
    providerId as AliyunCloudTtsProviderId,
  );
}

export function providerSelectorValue(providerId: string): string {
  return isAliyunCloudTtsProviderId(providerId)
    ? ALIYUN_BAILIAN_ENTRY_ID
    : providerId;
}

export function resolveAliyunCloudProviderId(
  providers: TtsProviderSnapshot[],
  currentProviderId: string,
  preferredProviderId?: AliyunCloudTtsProviderId,
): AliyunCloudTtsProviderId {
  if (isAliyunCloudTtsProviderId(currentProviderId)) return currentProviderId;

  const available = (providerId: AliyunCloudTtsProviderId) => {
    const provider = providers.find((item) => item.provider_id === providerId);
    return provider && provider.status !== "unavailable";
  };
  if (preferredProviderId && available(preferredProviderId))
    return preferredProviderId;

  const selected = providers.find(
    (provider) =>
      provider.selected && isAliyunCloudTtsProviderId(provider.provider_id),
  );
  if (selected && isAliyunCloudTtsProviderId(selected.provider_id))
    return selected.provider_id;

  return (
    ALIYUN_PROVIDER_IDS.find(available) ??
    preferredProviderId ??
    "aliyun_cosyvoice_realtime"
  );
}

export function resolveProviderSelection(
  selectionId: string,
  providers: TtsProviderSnapshot[],
  currentProviderId: string,
  preferredProviderId?: AliyunCloudTtsProviderId,
): string {
  return selectionId === ALIYUN_BAILIAN_ENTRY_ID
    ? resolveAliyunCloudProviderId(
        providers,
        currentProviderId,
        preferredProviderId,
      )
    : selectionId;
}

export function buildTtsProviderChoices(
  providers: TtsProviderSnapshot[],
  currentProviderId: string,
  preferredProviderId?: AliyunCloudTtsProviderId,
): TtsProviderChoice[] {
  const bailianProviders = providers.filter((provider) =>
    isAliyunCloudTtsProviderId(provider.provider_id),
  );
  const actualProviderId = resolveAliyunCloudProviderId(
    providers,
    currentProviderId,
    preferredProviderId,
  );
  const presented =
    bailianProviders.find(
      (provider) => provider.provider_id === actualProviderId,
    ) ??
    bailianProviders[0];
  const choices: TtsProviderChoice[] = [];
  let bailianAdded = false;

  for (const provider of providers) {
    if (!isAliyunCloudTtsProviderId(provider.provider_id)) {
      choices.push({
        id: provider.provider_id,
        displayName: provider.display_name,
        model: provider.model,
        languages: provider.languages,
        status: provider.status,
        modelLoaded: provider.model_loaded,
        selected: provider.provider_id === currentProviderId,
        actualProviderId: provider.provider_id,
      });
      continue;
    }
    if (bailianAdded) continue;
    bailianAdded = true;
    if (!presented) continue;
    choices.push({
      id: ALIYUN_BAILIAN_ENTRY_ID,
      displayName: "阿里云百炼",
      model: presented.model,
      languages: presented.languages,
      status: presented.status,
      modelLoaded: bailianProviders.some((item) => item.model_loaded),
      selected: isAliyunCloudTtsProviderId(currentProviderId),
      actualProviderId,
      engineLabel:
        presented.provider_id === "aliyun_cosyvoice_realtime"
          ? "CosyVoice"
          : "Qwen3-TTS VC",
    });
  }
  return choices;
}

export function readAliyunTtsPreference():
  | AliyunCloudTtsProviderId
  | undefined {
  if (typeof window === "undefined") return undefined;
  const value =
    window.localStorage?.getItem(ALIYUN_TTS_PREFERENCE_KEY) ?? "";
  return isAliyunCloudTtsProviderId(value) ? value : undefined;
}

export function saveAliyunTtsPreference(
  providerId: AliyunCloudTtsProviderId,
): void {
  if (typeof window === "undefined") return;
  window.localStorage?.setItem(ALIYUN_TTS_PREFERENCE_KEY, providerId);
}
