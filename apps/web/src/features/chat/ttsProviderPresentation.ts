import type { TtsProviderSnapshot } from "./types";

const TTS_GROUP_PREFERENCE_PREFIX =
  "chatwaifu.next.tts_provider_group_preference.";
const TTS_GROUP_SELECTOR_PREFIX = "tts-provider-group:";

export type TtsProviderPreferences = Readonly<Record<string, string>>;

export interface TtsProviderChoice {
  id: string;
  displayName: string;
  model: string;
  languages: string[];
  status: TtsProviderSnapshot["status"];
  modelLoaded: boolean;
  selected: boolean;
  actualProviderId: string;
  groupId?: string;
  variantLabel?: string;
}

export function providerSelectorValue(
  providers: TtsProviderSnapshot[],
  providerId: string,
): string {
  const provider = providers.find((item) => item.provider_id === providerId);
  const groupId = provider ? providerGroupId(provider) : undefined;
  return groupId ? groupSelectorId(groupId) : providerId;
}

export function resolveProviderSelection(
  selectionId: string,
  providers: TtsProviderSnapshot[],
  currentProviderId: string,
  preferences: TtsProviderPreferences = {},
): string {
  const groupId = groupIdForSelector(providers, selectionId);
  if (!groupId) return selectionId;
  return resolveGroupProviderId(
    providers,
    groupId,
    currentProviderId,
    preferences[groupId],
  );
}

export function buildTtsProviderChoices(
  providers: TtsProviderSnapshot[],
  currentProviderId: string,
  preferences: TtsProviderPreferences = {},
): TtsProviderChoice[] {
  const choices: TtsProviderChoice[] = [];
  const emittedGroups = new Set<string>();

  for (const provider of providers) {
    const groupId = providerGroupId(provider);
    if (!groupId) {
      choices.push(providerChoice(provider, currentProviderId));
      continue;
    }
    if (emittedGroups.has(groupId)) continue;
    emittedGroups.add(groupId);

    const actualProviderId = resolveGroupProviderId(
      providers,
      groupId,
      currentProviderId,
      preferences[groupId],
    );
    const presented =
      providers.find((item) => item.provider_id === actualProviderId) ??
      provider;
    choices.push({
      ...providerChoice(presented, currentProviderId),
      id: groupSelectorId(groupId),
      displayName:
        presented.presentation?.group_display_name ?? presented.display_name,
      selected:
        providerSelectorValue(providers, currentProviderId) ===
        groupSelectorId(groupId),
      actualProviderId,
      groupId,
      variantLabel: presented.presentation?.variant_label ?? undefined,
    });
  }
  return choices;
}

export function readTtsProviderPreferences(
  providers: TtsProviderSnapshot[],
): Record<string, string> {
  if (typeof window === "undefined") return {};
  const preferences: Record<string, string> = {};
  for (const groupId of providerGroupIds(providers)) {
    try {
      const providerId =
        window.localStorage?.getItem(preferenceStorageKey(groupId)) ?? "";
      if (
        providers.some(
          (provider) =>
            provider.provider_id === providerId &&
            providerGroupId(provider) === groupId,
        )
      ) {
        preferences[groupId] = providerId;
      }
    } catch {
      // Storage can be unavailable in privacy-constrained WebViews. Runtime
      // selection remains authoritative, so failing closed only loses a hint.
    }
  }
  return preferences;
}

export function saveTtsProviderPreference(
  providers: TtsProviderSnapshot[],
  providerId: string,
): void {
  if (typeof window === "undefined") return;
  const provider = providers.find((item) => item.provider_id === providerId);
  const groupId = provider ? providerGroupId(provider) : undefined;
  if (!groupId) return;
  try {
    window.localStorage?.setItem(preferenceStorageKey(groupId), providerId);
  } catch {
    // See readTtsProviderPreferences: persistence is best-effort UI state.
  }
}

function resolveGroupProviderId(
  providers: TtsProviderSnapshot[],
  groupId: string,
  currentProviderId: string,
  preferredProviderId?: string,
): string {
  const grouped = providers.filter(
    (provider) => providerGroupId(provider) === groupId,
  );
  if (!grouped.length) return currentProviderId;

  const current = grouped.find(
    (provider) => provider.provider_id === currentProviderId,
  );
  if (current) return current.provider_id;

  const orderedCandidates = [
    grouped.find((provider) => provider.provider_id === preferredProviderId),
    grouped.find((provider) => provider.selected),
    grouped.find((provider) => provider.presentation?.group_default),
    ...grouped,
  ].filter((provider): provider is TtsProviderSnapshot => Boolean(provider));
  return (
    orderedCandidates.find((provider) => provider.status !== "unavailable") ??
    orderedCandidates[0] ??
    grouped[0]
  ).provider_id;
}

function providerChoice(
  provider: TtsProviderSnapshot,
  currentProviderId: string,
): TtsProviderChoice {
  return {
    id: provider.provider_id,
    displayName: provider.display_name,
    model: provider.model,
    languages: provider.languages,
    status: provider.status,
    modelLoaded: provider.model_loaded,
    selected: provider.provider_id === currentProviderId,
    actualProviderId: provider.provider_id,
  };
}

function providerGroupId(provider: TtsProviderSnapshot): string | undefined {
  const presentation = provider.presentation;
  return presentation?.group_id &&
    presentation.group_display_name &&
    presentation.variant_label
    ? presentation.group_id
    : undefined;
}

function providerGroupIds(providers: TtsProviderSnapshot[]): string[] {
  return [
    ...new Set(
      providers
        .map((provider) => providerGroupId(provider))
        .filter((groupId): groupId is string => Boolean(groupId)),
    ),
  ];
}

function groupIdForSelector(
  providers: TtsProviderSnapshot[],
  selectionId: string,
): string | undefined {
  return providerGroupIds(providers).find(
    (groupId) => groupSelectorId(groupId) === selectionId,
  );
}

function groupSelectorId(groupId: string): string {
  return `${TTS_GROUP_SELECTOR_PREFIX}${groupId}`;
}

function preferenceStorageKey(groupId: string): string {
  return `${TTS_GROUP_PREFERENCE_PREFIX}${encodeURIComponent(groupId)}`;
}
