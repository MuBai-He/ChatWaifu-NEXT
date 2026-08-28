import { describe, expect, it } from "vitest";

import type { TtsProviderSnapshot } from "./types";
import {
  ALIYUN_BAILIAN_ENTRY_ID,
  buildTtsProviderChoices,
  providerSelectorValue,
  resolveProviderSelection,
} from "./ttsProviderPresentation";

const providers: TtsProviderSnapshot[] = [
  provider("qwen3_tts_mlx", "Qwen3-TTS · 本地", "local-qwen"),
  provider(
    "aliyun_qwen_realtime",
    "阿里云百炼 · Qwen3-TTS VC",
    "qwen3-tts-vc-realtime-2026-01-15",
  ),
  provider(
    "aliyun_cosyvoice_realtime",
    "阿里云百炼 · CosyVoice",
    "cosyvoice-v3.5-plus",
  ),
];

describe("TTS provider presentation", () => {
  it("groups both Bailian adapters into one selectable entry", () => {
    const choices = buildTtsProviderChoices(
      providers,
      "qwen3_tts_mlx",
      "aliyun_cosyvoice_realtime",
    );

    expect(choices.map((choice) => choice.id)).toEqual([
      "qwen3_tts_mlx",
      ALIYUN_BAILIAN_ENTRY_ID,
    ]);
    expect(choices[1]).toMatchObject({
      displayName: "阿里云百炼",
      engineLabel: "CosyVoice",
      model: "cosyvoice-v3.5-plus",
    });
  });

  it("keeps the real adapter id at the Runtime boundary", () => {
    expect(providerSelectorValue("aliyun_qwen_realtime")).toBe(
      ALIYUN_BAILIAN_ENTRY_ID,
    );
    expect(
      resolveProviderSelection(
        ALIYUN_BAILIAN_ENTRY_ID,
        providers,
        "qwen3_tts_mlx",
        "aliyun_qwen_realtime",
      ),
    ).toBe("aliyun_qwen_realtime");
  });
});

function provider(
  providerId: string,
  displayName: string,
  model: string,
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
    local_only: !providerId.startsWith("aliyun_"),
    status: "ready",
    model_loaded: false,
    queue_depth: 0,
    selected: providerId === "qwen3_tts_mlx",
  };
}
