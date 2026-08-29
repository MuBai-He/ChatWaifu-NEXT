import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TtsProviderSnapshot } from "./types";
import {
  buildTtsProviderChoices,
  providerSelectorValue,
  readTtsProviderPreferences,
  resolveProviderSelection,
  saveTtsProviderPreference,
} from "./ttsProviderPresentation";

const providers: TtsProviderSnapshot[] = [
  provider("local_voice", "Local Voice", "local-v1"),
  provider("alpha_fast", "Alpha Fast", "alpha-fast-v1", {
    groupId: "cloud_alpha",
    groupName: "Cloud Alpha",
    variantLabel: "Fast",
  }),
  provider("alpha_quality", "Alpha Quality", "alpha-quality-v2", {
    groupId: "cloud_alpha",
    groupName: "Cloud Alpha",
    variantLabel: "Quality",
    groupDefault: true,
  }),
  provider("beta_standard", "Beta Standard", "beta-v1", {
    groupId: "cloud_beta",
    groupName: "Cloud Beta",
    variantLabel: "Standard",
    groupDefault: true,
  }),
];

describe("TTS provider presentation", () => {
  beforeEach(() => vi.stubGlobal("localStorage", memoryStorage()));
  afterEach(() => vi.unstubAllGlobals());

  it("groups arbitrary providers only from Runtime presentation metadata", () => {
    const choices = buildTtsProviderChoices(providers, "local_voice", {
      cloud_alpha: "alpha_fast",
    });

    expect(choices).toHaveLength(3);
    expect(choices[1]).toMatchObject({
      id: providerSelectorValue(providers, "alpha_fast"),
      displayName: "Cloud Alpha",
      variantLabel: "Fast",
      model: "alpha-fast-v1",
      actualProviderId: "alpha_fast",
    });
    expect(choices[2]).toMatchObject({
      displayName: "Cloud Beta",
      variantLabel: "Standard",
      actualProviderId: "beta_standard",
    });
  });

  it("resolves a grouped selector to the preference and then metadata default", () => {
    const selectionId = providerSelectorValue(providers, "alpha_fast");

    expect(
      resolveProviderSelection(selectionId, providers, "local_voice", {
        cloud_alpha: "alpha_fast",
      }),
    ).toBe("alpha_fast");
    expect(
      resolveProviderSelection(selectionId, providers, "local_voice"),
    ).toBe("alpha_quality");
  });

  it("persists an independent provider preference for every group", () => {
    saveTtsProviderPreference(providers, "alpha_fast");
    saveTtsProviderPreference(providers, "beta_standard");

    expect(readTtsProviderPreferences(providers)).toEqual({
      cloud_alpha: "alpha_fast",
      cloud_beta: "beta_standard",
    });
  });
});

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

function provider(
  providerId: string,
  displayName: string,
  model: string,
  group?: {
    groupId: string;
    groupName: string;
    variantLabel: string;
    groupDefault?: boolean;
  },
): TtsProviderSnapshot {
  return {
    provider_id: providerId,
    display_name: displayName,
    model,
    languages: ["zh", "ja"],
    supports_voice_cloning: true,
    supports_style: false,
    supports_speed: true,
    supports_pitch: false,
    native_streaming: true,
    local_only: !group,
    status: "ready",
    model_loaded: false,
    queue_depth: 0,
    selected: providerId === "local_voice",
    presentation: group
      ? {
          group_id: group.groupId,
          group_display_name: group.groupName,
          variant_label: group.variantLabel,
          group_default: group.groupDefault ?? false,
        }
      : null,
  };
}
