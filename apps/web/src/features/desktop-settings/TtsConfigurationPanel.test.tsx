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
import { TtsConfigurationPanel } from "./TtsConfigurationPanel";

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
    vi.mocked(runtimeClient.updateTtsConfiguration).mockImplementation(
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
        preferredProviderId="cloud_beta_voice"
        onProviderIdChange={onProviderIdChange}
        onSaved={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("region", { name: "TTS Provider 设置" }),
    ).toBeTruthy();
    expect(screen.getAllByText("Cloud Beta Voice").length).toBeGreaterThan(0);
    expect(screen.getByText("基础情绪指令")).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox", { name: "TTS 配置入口" }), {
      target: { value: "cloud_alpha_voice" },
    });
    expect(onProviderIdChange).toHaveBeenCalledWith("cloud_alpha_voice");
    await waitFor(() =>
      expect(runtimeClient.getTtsConfiguration).toHaveBeenCalledWith(
        "cloud_alpha_voice",
      ),
    );
    await waitFor(() =>
      expect(screen.getAllByText("Cloud Alpha Voice").length).toBeGreaterThan(
        0,
      ),
    );
    expect(screen.queryByText("基础情绪指令")).toBeNull();
  });

  it("uses credential metadata for configured state and clear payload", async () => {
    render(
      <TtsConfigurationPanel
        preferredProviderId="cloud_alpha_voice"
        onSaved={vi.fn()}
      />,
    );

    expect(
      await screen.findByText("Access Token · 已在 Runtime 本地配置"),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByLabelText("移除此 Provider 单独保存的 Access Token"),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() =>
      expect(runtimeClient.updateTtsConfiguration).toHaveBeenCalledWith(
        "cloud_alpha_voice",
        expect.objectContaining({ revoke_token: true }),
      ),
    );
    const payload = vi.mocked(runtimeClient.updateTtsConfiguration).mock
      .calls[0]?.[1];
    expect(payload).not.toHaveProperty("clear_api_key");
  });
});

function registrations(): TtsConfigurationRegistration[] {
  return [
    registration("cloud_alpha_voice", "Cloud Alpha Voice", [
      field("enabled", "启用", "toggle"),
      field("model", "模型", "text"),
      field("api_key", "Access Token", "secret"),
    ]),
    registration("cloud_beta_voice", "Cloud Beta Voice", [
      field("enabled", "启用", "toggle"),
      field("model", "模型", "text"),
      field("instruction", "基础情绪指令", "textarea"),
      field("api_key", "Access Token", "secret"),
    ]),
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
    configuration_schema_version: "1.0",
    configuration_schema: { properties: {}, required: [] },
    ui_schema: { schema_version: "1.0", fields },
    credential: {
      kind: "api_key",
      field_key: "api_key",
      configured_field: "token_present",
      clear_field: "revoke_token",
      fallback_provider_id: null,
    },
    presentation: {
      group_id: "cloud_suite",
      group_display_name: "Cloud Suite",
      variant_label: providerId.includes("alpha") ? "Alpha" : "Beta",
      group_default: providerId.includes("beta"),
    },
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
    model: providerId.includes("beta") ? "beta-v2" : "alpha-v1",
    instruction: providerId.includes("beta") ? "温柔" : "",
    token_present: true,
    updated_at: new Date(0).toISOString(),
  };
}
