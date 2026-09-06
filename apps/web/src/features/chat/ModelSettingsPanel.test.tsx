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
  rebuildIndexes: vi.fn(),
  getIndexRebuildStatus: vi.fn(),
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
    vi.mocked(runtimeClient.getIndexRebuildStatus).mockResolvedValue({
      schema_version: "1.0",
      job_id: "job-0",
      state: "idle",
      domains: {
        memory: {
          domain: "memory",
          state: "idle",
          total_count: 0,
          indexed_count: 0,
          failed_count: 0,
        },
        photo: {
          domain: "photo",
          state: "idle",
          total_count: 0,
          indexed_count: 0,
          failed_count: 0,
        },
      },
    });
    vi.mocked(runtimeClient.rebuildIndexes).mockResolvedValue({
      schema_version: "1.0",
      job_id: "job-1",
      state: "running",
      domains: {
        memory: {
          domain: "memory",
          state: "running",
          total_count: 5,
          indexed_count: 0,
          failed_count: 0,
        },
        photo: {
          domain: "photo",
          state: "running",
          total_count: 2,
          indexed_count: 0,
          failed_count: 0,
        },
      },
    });
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

  it("displays warning modal with EXACT text when embedding model is changed", async () => {
    render(
      <ModelSettingsPanel sessionId="00000000-0000-4000-8000-000000000001" />,
    );

    const embeddingModelInput = await screen.findByRole("textbox", {
      name: "Embedding 模型 模型 ID",
    });
    const embeddingCard = embeddingModelInput.closest("section");
    if (!embeddingCard) throw new Error("expected embedding card");

    fireEvent.change(embeddingModelInput, {
      target: { value: "text-embedding-3-small" },
    });

    const saveButton = Array.from(
      embeddingCard.querySelectorAll("button"),
    ).find((b) => b.textContent === "保存");
    if (!saveButton) throw new Error("expected save button");

    fireEvent.click(saveButton);

    const exactWarning =
      "embedding 模型已更换，现有索引仍由旧模型生成。不重建可能导致漏检、错误匹配或相关度下降；向量维度不兼容的条目将无法参与语义检索。建议重建索引。";
    const modalText = await screen.findByText(exactWarning);
    expect(modalText).toBeTruthy();
    expect(screen.getByRole("button", { name: "稍后" })).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    const later = screen.getByRole("button", { name: "稍后" });
    expect(document.activeElement).toBe(later);
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement?.textContent).toBe("重建索引");
    expect(runtimeClient.rebuildIndexes).not.toHaveBeenCalled();
  });

  it("does NOT display warning modal on secret change, timeout change, or other model roles", async () => {
    render(
      <ModelSettingsPanel sessionId="00000000-0000-4000-8000-000000000001" />,
    );

    // 1. Changing chat model
    const chatModel = await screen.findByRole("textbox", {
      name: "聊天模型 模型 ID",
    });
    const chatCard = chatModel.closest("section");
    if (!chatCard) throw new Error("expected chat card");
    fireEvent.change(chatModel, { target: { value: "chat-v2" } });
    const chatSave = Array.from(chatCard.querySelectorAll("button")).find(
      (b) => b.textContent === "保存",
    );
    fireEvent.click(chatSave!);
    await screen.findByText("聊天模型已保存");
    expect(screen.queryByRole("dialog")).toBeNull();

    // 2. Changing embedding timeout only
    const embeddingModel = screen.getByRole("textbox", {
      name: "Embedding 模型 模型 ID",
    });
    const embeddingCard = embeddingModel.closest("section");
    if (!embeddingCard) throw new Error("expected embedding card");
    const timeoutInput = embeddingCard.querySelectorAll(
      "input[type='number']",
    )[1];
    fireEvent.change(timeoutInput, { target: { value: "120" } });
    const embeddingSave = Array.from(
      embeddingCard.querySelectorAll("button"),
    ).find((b) => b.textContent === "保存");
    fireEvent.click(embeddingSave!);
    await screen.findByText("Embedding 模型已保存");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("dismisses warning modal on '稍后' or Escape without triggering rebuild", async () => {
    render(
      <ModelSettingsPanel sessionId="00000000-0000-4000-8000-000000000001" />,
    );

    const embeddingModel = await screen.findByRole("textbox", {
      name: "Embedding 模型 模型 ID",
    });
    const embeddingCard = embeddingModel.closest("section");
    if (!embeddingCard) throw new Error("expected embedding card");

    // 1. Click "稍后"
    fireEvent.change(embeddingModel, { target: { value: "model-a" } });
    const saveButton = Array.from(
      embeddingCard.querySelectorAll("button"),
    ).find((b) => b.textContent === "保存");
    fireEvent.click(saveButton!);

    const laterButton = await screen.findByRole("button", { name: "稍后" });
    fireEvent.click(laterButton);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(runtimeClient.rebuildIndexes).not.toHaveBeenCalled();

    // 2. Press Escape
    fireEvent.change(embeddingModel, { target: { value: "model-b" } });
    fireEvent.click(saveButton!);
    await screen.findByRole("dialog");
    fireEvent.keyDown(screen.getByRole("button", { name: "稍后" }), {
      key: "Escape",
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(runtimeClient.rebuildIndexes).not.toHaveBeenCalled();
  });

  it("triggers rebuildIndexes singleflight from manual button or modal button", async () => {
    render(
      <ModelSettingsPanel sessionId="00000000-0000-4000-8000-000000000001" />,
    );

    const embeddingModel = await screen.findByRole("textbox", {
      name: "Embedding 模型 模型 ID",
    });
    const embeddingCard = embeddingModel.closest("section");
    if (!embeddingCard) throw new Error("expected embedding card");

    // Manual button in footer
    const manualRebuildBtn = Array.from(
      embeddingCard.querySelectorAll("button"),
    ).find((b) => b.textContent === "重建索引");
    if (!manualRebuildBtn) throw new Error("expected manual rebuild button");

    fireEvent.click(manualRebuildBtn);

    await waitFor(() =>
      expect(runtimeClient.rebuildIndexes).toHaveBeenCalledTimes(1),
    );
    expect(manualRebuildBtn.textContent).toBe("重建中…");
    expect(manualRebuildBtn.disabled).toBe(true);

    // Clicking while disabled/running should not trigger a second rebuild
    fireEvent.click(manualRebuildBtn);
    expect(runtimeClient.rebuildIndexes).toHaveBeenCalledTimes(1);
  });

  it("triggers rebuildIndexes when confirming from warning modal", async () => {
    render(
      <ModelSettingsPanel sessionId="00000000-0000-4000-8000-000000000001" />,
    );

    const embeddingModel = await screen.findByRole("textbox", {
      name: "Embedding 模型 模型 ID",
    });
    const embeddingCard = embeddingModel.closest("section");
    if (!embeddingCard) throw new Error("expected embedding card");

    fireEvent.change(embeddingModel, { target: { value: "model-new" } });
    const saveButton = Array.from(
      embeddingCard.querySelectorAll("button"),
    ).find((b) => b.textContent === "保存");
    fireEvent.click(saveButton!);

    const modal = await screen.findByRole("dialog");
    const confirmButton = Array.from(modal.querySelectorAll("button")).find(
      (b) => b.textContent === "重建索引",
    );
    if (!confirmButton) throw new Error("expected confirm button in modal");

    fireEvent.click(confirmButton);

    await waitFor(() =>
      expect(runtimeClient.rebuildIndexes).toHaveBeenCalledTimes(1),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("does not warn or rebuild on failed or unchanged saves", async () => {
    render(<ModelSettingsPanel sessionId={null} />);
    const input = await screen.findByRole("textbox", {
      name: "Embedding 模型 模型 ID",
    });
    const save = Array.from(
      input.closest("section")!.querySelectorAll("button"),
    ).find((b) => b.textContent === "保存")!;
    fireEvent.click(save);
    await screen.findByText("Embedding 模型已保存");
    expect(screen.queryByRole("dialog")).toBeNull();
    vi.mocked(runtimeClient.updateModelConfiguration).mockRejectedValueOnce(
      new Error("保存失败测试"),
    );
    fireEvent.change(input, { target: { value: "not-saved" } });
    fireEvent.click(save);
    await screen.findByText("保存失败测试");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(runtimeClient.rebuildIndexes).not.toHaveBeenCalled();
  });

  it("does not warn or rebuild when only the embedding key changes", async () => {
    vi.mocked(runtimeClient.getModelConfigurations).mockResolvedValue(
      configurations.map((c) =>
        c.role === "embedding"
          ? model("embedding", "openai_compatible", "text-v1", true)
          : c,
      ),
    );
    render(<ModelSettingsPanel sessionId={null} />);
    const key = await screen.findByLabelText("Embedding 模型 API Key");
    fireEvent.change(key, { target: { value: "fixture-key" } });
    const input = screen.getByRole("textbox", {
      name: "Embedding 模型 模型 ID",
    });
    const save = Array.from(
      input.closest("section")!.querySelectorAll("button"),
    ).find((b) => b.textContent === "保存")!;
    fireEvent.click(save);
    await screen.findByText("Embedding 模型已保存");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(runtimeClient.rebuildIndexes).not.toHaveBeenCalled();
    expect(screen.queryByDisplayValue("fixture-key")).toBeNull();
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
