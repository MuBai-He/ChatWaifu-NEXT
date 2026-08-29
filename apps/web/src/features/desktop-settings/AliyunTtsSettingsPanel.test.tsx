import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as runtimeClient from "../chat/runtimeClient";
import type {
  TtsConfigurationRegistration,
  TtsConfigurationSnapshot,
} from "../chat/types";
import { TtsConfigurationPanel } from "./AliyunTtsSettingsPanel";

vi.mock("../chat/runtimeClient", () => ({
  getTtsConfiguration: vi.fn(),
  getTtsConfigurationRegistrations: vi.fn(),
  testTtsConfiguration: vi.fn(),
  updateTtsConfiguration: vi.fn(),
}));

describe("TtsConfigurationPanel", () => {
  beforeEach(() => {
    vi.mocked(runtimeClient.getTtsConfigurationRegistrations).mockResolvedValue(
      registrations(),
    );
    vi.mocked(runtimeClient.getTtsConfiguration).mockImplementation(
      (providerId) => Promise.resolve(configuration(providerId)),
    );
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders provider fields from the Runtime registry and switches entries", async () => {
    const onProviderIdChange = vi.fn();
    render(
      <TtsConfigurationPanel
        preferredProviderId="aliyun_cosyvoice_realtime"
        onProviderIdChange={onProviderIdChange}
        onSaved={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("region", { name: "TTS Provider 设置" }),
    ).toBeTruthy();
    expect(
      screen.getAllByText("阿里云百炼 · CosyVoice 实时音色").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("基础情绪指令")).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox", { name: "TTS 配置入口" }), {
      target: { value: "aliyun_qwen_realtime" },
    });
    expect(onProviderIdChange).toHaveBeenCalledWith("aliyun_qwen_realtime");
    await waitFor(() =>
      expect(runtimeClient.getTtsConfiguration).toHaveBeenCalledWith(
        "aliyun_qwen_realtime",
      ),
    );
    await waitFor(() =>
      expect(
        screen.getAllByText("阿里云百炼 · Qwen3-TTS 实时音色").length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByText("基础情绪指令")).toBeNull();
  });
});

function registrations(): TtsConfigurationRegistration[] {
  return [
    registration("aliyun_qwen_realtime", "阿里云百炼 · Qwen3-TTS 实时音色", [
      field("enabled", "启用", "toggle"),
      field("model", "模型", "text"),
      field("api_key", "API Key", "secret"),
    ]),
    registration(
      "aliyun_cosyvoice_realtime",
      "阿里云百炼 · CosyVoice 实时音色",
      [
        field("enabled", "启用", "toggle"),
        field("model", "模型", "text"),
        field("instruction", "基础情绪指令", "textarea"),
        field("api_key", "API Key", "secret"),
      ],
    ),
  ];
}

function registration(
  providerId: string,
  displayName: string,
  fields: TtsConfigurationRegistration["ui_schema"]["fields"],
): TtsConfigurationRegistration {
  return {
    provider_id: providerId,
    display_name: displayName,
    configuration_schema: { properties: {}, required: [] },
    ui_schema: { schema_version: "1.0", fields },
    configuration: configuration(providerId),
  };
}

function field(
  key: string,
  label: string,
  control: TtsConfigurationRegistration["ui_schema"]["fields"][number]["control"],
): TtsConfigurationRegistration["ui_schema"]["fields"][number] {
  return {
    key,
    label,
    control,
    advanced: false,
    options: [],
    minimum: null,
    maximum: null,
    step: null,
    placeholder: "",
    help_text: "",
  };
}

function configuration(providerId: string): TtsConfigurationSnapshot {
  return {
    provider_id: providerId,
    enabled: true,
    model: providerId.includes("cosyvoice")
      ? "cosyvoice-v3.5-plus"
      : "qwen3-tts-vc-realtime-2026-01-15",
    instruction: providerId.includes("cosyvoice") ? "温柔" : "",
    api_key_configured: true,
    updated_at: new Date(0).toISOString(),
  };
}
