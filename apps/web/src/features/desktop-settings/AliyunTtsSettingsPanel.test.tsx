import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as runtimeClient from "../chat/runtimeClient";
import type { AliyunCloudTtsConfiguration } from "../chat/types";
import { AliyunTtsSettingsPanel } from "./AliyunTtsSettingsPanel";

vi.mock("../chat/runtimeClient", () => ({
  getAliyunTtsConfiguration: vi.fn(),
  testAliyunTtsConfiguration: vi.fn(),
  updateAliyunTtsConfiguration: vi.fn(),
}));

describe("AliyunTtsSettingsPanel", () => {
  beforeEach(() => {
    vi.mocked(runtimeClient.getAliyunTtsConfiguration).mockImplementation(
      (providerId) => Promise.resolve(configuration(providerId)),
    );
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("uses one Bailian panel and switches the concrete API inside it", async () => {
    const onProviderIdChange = vi.fn();
    const { rerender } = render(
      <AliyunTtsSettingsPanel
        providerId="aliyun_cosyvoice_realtime"
        onProviderIdChange={onProviderIdChange}
        onSaved={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("region", { name: "阿里云百炼 API 设置" }),
    ).toBeTruthy();
    expect(screen.getAllByText("阿里云百炼")).toHaveLength(1);
    expect(screen.getByText(/CosyVoice 3.5 · 实时情绪复刻/)).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox", { name: "百炼语音 API" }), {
      target: { value: "aliyun_qwen_realtime" },
    });
    expect(onProviderIdChange).toHaveBeenCalledWith("aliyun_qwen_realtime");

    rerender(
      <AliyunTtsSettingsPanel
        providerId="aliyun_qwen_realtime"
        onProviderIdChange={onProviderIdChange}
        onSaved={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/Qwen3-TTS VC · 实时复刻/)).toBeTruthy(),
    );
    expect(
      screen.getAllByRole("region", { name: "阿里云百炼 API 设置" }),
    ).toHaveLength(1);
  });
});

function configuration(
  providerId: "aliyun_qwen_realtime" | "aliyun_cosyvoice_realtime",
): AliyunCloudTtsConfiguration {
  const common = {
    enabled: true,
    voice_id: "test-voice",
    region: "beijing" as const,
    workspace_id: "",
    sample_rate: 24000 as const,
    speech_rate: 1,
    volume: 50,
    pitch_rate: 1,
    timeout_seconds: 30,
    max_audio_bytes: 1048576,
    api_key_configured: true,
    updated_at: new Date(0).toISOString(),
  };
  return providerId === "aliyun_cosyvoice_realtime"
    ? {
        ...common,
        provider_id: providerId,
        model: "cosyvoice-v3.5-plus",
        language_type: "auto",
        instruction: "温柔",
      }
    : {
        ...common,
        provider_id: providerId,
        model: "qwen3-tts-vc-realtime-2026-01-15",
        language_type: "Auto",
        instruction: "",
      };
}
