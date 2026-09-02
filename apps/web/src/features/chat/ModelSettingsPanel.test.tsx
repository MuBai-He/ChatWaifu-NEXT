import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelSettingsPanel } from "./ModelSettingsPanel";
import * as runtimeClient from "./runtimeClient";
import type { ModelRoleConfiguration } from "./types";

vi.mock("./runtimeClient", () => ({
  getCharacterState: vi.fn(),
  getModelConfigurations: vi.fn(),
  testModelConfiguration: vi.fn(),
  updateModelConfiguration: vi.fn(),
}));

const configurations: ModelRoleConfiguration[] = [
  model("chat", "demo", "demo-chat"),
  model("memory_extraction", "openai_compatible", "extract-v1", true),
  model("memory_summary", "demo", "summary-v1"),
  model("embedding", "local_hash", "local-hash-64-v1"),
];

describe("ModelSettingsPanel", () => {
  beforeEach(() => {
    vi.mocked(runtimeClient.getModelConfigurations).mockResolvedValue(
      configurations,
    );
    vi.mocked(runtimeClient.getCharacterState).mockResolvedValue({
      character_id: "default",
      user_scope: "local",
      revision: 3,
      affect: {
        valence: 0.4,
        arousal: 0.25,
        energy: 0.65,
        attention: 0.7,
        embarrassment: 0.1,
        tension: 0.05,
        updated_at: new Date().toISOString(),
      },
      relationship: {
        familiarity: 0.35,
        trust: 0.3,
        affinity: 0.38,
        comfort: 0.32,
        recent_tension: 0,
        interaction_count: 5,
        stage: "familiar",
        preferred_address: null,
        updated_at: new Date().toISOString(),
      },
    });
    vi.mocked(runtimeClient.updateModelConfiguration).mockImplementation(
      (role, value) =>
        Promise.resolve({
          ...configurations.find((item) => item.role === role)!,
          ...value,
          role,
          api_key_configured: Boolean(value.api_key),
          updated_at: new Date().toISOString(),
        }),
    );
    vi.mocked(runtimeClient.testModelConfiguration).mockResolvedValue({
      role: "chat",
      status: "ok",
      characters: 12,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("edits one model role without coupling it to chat and never displays a saved key", async () => {
    render(
      <ModelSettingsPanel sessionId="00000000-0000-4000-8000-000000000001" />,
    );

    expect(await screen.findByText("熟悉 · #3")).toBeTruthy();
    const extractionModel = screen.getByRole("textbox", {
      name: "记忆提取模型 模型 ID",
    });
    const chatModel = screen.getByRole("textbox", {
      name: "聊天模型 模型 ID",
    });
    fireEvent.change(extractionModel, { target: { value: "extract-v2" } });
    fireEvent.change(screen.getByLabelText("记忆提取模型 API Key"), {
      target: { value: "secret-test-value" },
    });
    const extractionCard = extractionModel.closest("section");
    if (!extractionCard) throw new Error("expected extraction card");
    const saveButton = Array.from(
      extractionCard.querySelectorAll("button"),
    ).find((button) => button.textContent === "保存");
    if (!saveButton) throw new Error("expected save button");
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(runtimeClient.updateModelConfiguration).toHaveBeenCalledWith(
        "memory_extraction",
        expect.objectContaining({
          model: "extract-v2",
          api_key: "secret-test-value",
        }),
      ),
    );
    expect(chatModel).toBeInstanceOf(HTMLInputElement);
    if (!(chatModel instanceof HTMLInputElement))
      throw new Error("expected chat model input");
    expect(chatModel.value).toBe("demo-chat");
    expect(screen.queryByDisplayValue("secret-test-value")).toBeNull();
  });

  it("keeps model tests clickable while the sticky save notice is visible", async () => {
    render(
      <ModelSettingsPanel sessionId="00000000-0000-4000-8000-000000000001" />,
    );

    const chatModel = await screen.findByRole("textbox", {
      name: "聊天模型 模型 ID",
    });
    const chatCard = chatModel.closest("section");
    if (!chatCard) throw new Error("expected chat model card");
    const saveButton = Array.from(chatCard.querySelectorAll("button")).find(
      (button) => button.textContent === "保存",
    );
    const testButton = Array.from(chatCard.querySelectorAll("button")).find(
      (button) => button.textContent === "测试",
    );
    if (!saveButton || !testButton)
      throw new Error("expected chat model actions");

    fireEvent.click(saveButton);
    const saveNotice = await screen.findByText("聊天模型已保存");
    expect(saveNotice.getAttribute("role")).toBe("status");
    expect(testButton.disabled).toBe(false);

    fireEvent.click(testButton);

    await waitFor(() =>
      expect(runtimeClient.testModelConfiguration).toHaveBeenCalledWith("chat"),
    );
    expect(
      await screen.findByText("聊天模型连接 ok，返回 12 字符"),
    ).toBeTruthy();
  });
});

function model(
  role: ModelRoleConfiguration["role"],
  provider: ModelRoleConfiguration["provider"],
  modelId: string,
  apiKeyConfigured = false,
): ModelRoleConfiguration {
  return {
    role,
    provider,
    model: modelId,
    base_url:
      provider === "openai_compatible" ? "http://127.0.0.1:9999/v1" : "",
    timeout_seconds: 60,
    context_window: 8192,
    enabled: true,
    api_key_configured: apiKeyConfigured,
    updated_at: new Date().toISOString(),
  };
}
