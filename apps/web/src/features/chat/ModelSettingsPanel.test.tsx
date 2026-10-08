import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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
  {
    ...model("behavior_decision", "inherit_chat", "inherit-chat"),
    timeout_seconds: 30,
  },
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

  it("retains drafts between model purposes and marks a saved compact card", async () => {
    render(<ModelSettingsPanel sessionId={null} compact />);
    const input = await screen.findByLabelText("聊天模型 模型 ID");
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存" }).disabled,
    ).toBe(true);
    fireEvent.change(input, { target: { value: "chat-draft" } });
    expect(screen.getByText("有未保存的修改")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "行为决策模型" }));
    expect(
      screen.getByRole("combobox", { name: "行为决策模型 Provider" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "聊天模型" }));
    expect(
      screen.getByRole<HTMLInputElement>("textbox", {
        name: "聊天模型 模型 ID",
      }).value,
    ).toBe("chat-draft");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "保存" })
          .disabled,
      ).toBe(true),
    );
    expect(runtimeClient.updateModelConfiguration).toHaveBeenCalledTimes(1);
    expect(runtimeClient.updateModelConfiguration).toHaveBeenCalledWith(
      "chat",
      expect.objectContaining({ model: "chat-draft" }),
    );
  });

  it("selects the native Jev adapter with its endpoint and separate key", async () => {
    render(<ModelSettingsPanel sessionId={null} />);
    const select = await screen.findByLabelText("行为决策模型 Provider");
    const card = select.closest("section");
    if (!card) throw new Error("missing decision card");
    fireEvent.change(select, { target: { value: "typesafe" } });
    expect(screen.getByDisplayValue("jev-latest")).toBeTruthy();
    expect(screen.getByDisplayValue("https://api.typesafe.ai/v1")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("行为决策模型 API Key"), {
      target: { value: "private-jev-key" },
    });
    fireEvent.click(within(card).getByText("保存", { selector: "button" }));
    await waitFor(() =>
      expect(runtimeClient.updateModelConfiguration).toHaveBeenCalledWith(
        "behavior_decision",
        expect.objectContaining({
          provider: "typesafe",
          model: "jev-latest",
          base_url: "https://api.typesafe.ai/v1",
          api_key: "private-jev-key",
          timeout_seconds: 10,
        }),
      ),
    );
    expect(screen.getByDisplayValue("demo-chat")).toBeTruthy();
  });

  it("saves an independent decision endpoint without changing chat and tests native decisions", async () => {
    vi.mocked(runtimeClient.testModelConfiguration).mockResolvedValue({
      status: "ok",
      action: "respond",
    });
    render(<ModelSettingsPanel sessionId={null} />);
    const select = await screen.findByLabelText("行为决策模型 Provider");
    const card = select.closest("section");
    if (!card) throw new Error("missing decision card");
    expect(within(card).getByText("跟随聊天模型")).toBeTruthy();
    expect(within(card).queryByLabelText("行为决策模型 API Key")).toBeNull();
    fireEvent.change(select, { target: { value: "openai_compatible" } });
    fireEvent.change(screen.getByLabelText("行为决策模型 模型 ID"), {
      target: { value: "decision-small" },
    });
    fireEvent.change(screen.getByLabelText("行为决策模型 Base URL"), {
      target: { value: "https://decision.example/v1" },
    });
    fireEvent.change(screen.getByLabelText("行为决策模型 API Key"), {
      target: { value: "private-decision-key" },
    });
    fireEvent.click(within(card).getByText("保存", { selector: "button" }));
    await waitFor(() =>
      expect(runtimeClient.updateModelConfiguration).toHaveBeenCalledWith(
        "behavior_decision",
        expect.objectContaining({
          provider: "openai_compatible",
          model: "decision-small",
          base_url: "https://decision.example/v1",
          api_key: "private-decision-key",
          timeout_seconds: 30,
        }),
      ),
    );
    expect(screen.getByDisplayValue("demo-chat")).toBeTruthy();
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("行为决策模型 API Key").value,
      ).toBe(""),
    );
    fireEvent.click(within(card).getByText("测试", { selector: "button" }));
    await waitFor(() =>
      expect(runtimeClient.testModelConfiguration).toHaveBeenCalledWith(
        "behavior_decision",
      ),
    );
    await screen.findByText(/结构化决策已验证/);
  });

  it("saves inheritance without sending a pending independent credential", async () => {
    render(<ModelSettingsPanel sessionId={null} />);
    const select = await screen.findByLabelText("行为决策模型 Provider");
    const card = select.closest("section");
    if (!card) throw new Error("missing decision card");
    fireEvent.change(select, { target: { value: "openai_compatible" } });
    fireEvent.change(screen.getByLabelText("行为决策模型 API Key"), {
      target: { value: "unused-key" },
    });
    fireEvent.change(select, { target: { value: "inherit_chat" } });
    fireEvent.click(within(card).getByText("保存", { selector: "button" }));
    await waitFor(() =>
      expect(runtimeClient.updateModelConfiguration).toHaveBeenCalled(),
    );
    const [role, body] = vi.mocked(runtimeClient.updateModelConfiguration).mock
      .calls[0];
    expect(role).toBe("behavior_decision");
    expect(body.provider).toBe("inherit_chat");
    expect(body.api_key).toBeUndefined();
  });

  it("saves the selected model budget and shows the reference input allowance", async () => {
    vi.mocked(runtimeClient.updateModelConfiguration).mockImplementation(
      (role, update) =>
        Promise.resolve({
          ...configurations.find((entry) => entry.role === role)!,
          ...update,
        }),
    );
    render(<ModelSettingsPanel sessionId={null} />);
    const chat = await screen.findByDisplayValue("demo-chat");
    const card = chat.closest("section");
    if (!card) throw new Error("expected chat model card");
    const field = (label: string) => {
      const node = Array.from(card.querySelectorAll("label"))
        .find((item) => item.querySelector("span")?.textContent === label)
        ?.querySelector("input,select");
      if (!node) throw new Error(`missing ${label}`);
      return node;
    };
    fireEvent.change(field("运行上下文窗口"), { target: { value: "32768" } });
    fireEvent.change(field("输出预留 token"), { target: { value: "8192" } });
    fireEvent.change(field("请求输出上限"), { target: { value: "8192" } });
    fireEvent.change(field("输入估算余量（%）"), { target: { value: "15" } });
    fireEvent.change(field("分项预算"), { target: { value: "scaled" } });
    // An intermediate invalid numeric edit must not crash the settings panel.
    fireEvent.change(field("本会话历史条数"), { target: { value: "0" } });
    fireEvent.change(field("本会话历史条数"), { target: { value: "32" } });
    fireEvent.change(field("记忆候选上限"), { target: { value: "24" } });
    fireEvent.change(field("工具正文上限（字节）"), {
      target: { value: "131072" },
    });
    expect(card.textContent).toContain("21370 参考 token");
    const save = Array.from(card.querySelectorAll("button")).find(
      (button) => button.textContent === "保存",
    );
    if (!save) throw new Error("missing save");
    fireEvent.click(save);
    await waitFor(() =>
      expect(runtimeClient.updateModelConfiguration).toHaveBeenCalled(),
    );
    const [savedRole, saved] = vi.mocked(runtimeClient.updateModelConfiguration)
      .mock.calls[0];
    expect(savedRole).toBe("chat");
    expect(saved.context_window).toBe(32768);
    expect(saved.budget).toMatchObject({
      output_reserve_tokens: 8192,
      max_output_tokens: 8192,
      estimate_margin_ratio: 0.15,
      section_policy: "scaled",
      history_turn_limit: 32,
      memory_candidate_limit: 24,
      tool_result_max_bytes: 131072,
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
    await waitFor(() => expect(document.activeElement).toBe(later));
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
    const dismissButton = screen.getByRole("button", { name: "稍后" });
    // The dialog's effect installs keyboard handling and transfers focus.
    // DOM insertion alone does not establish that the modal is interactive.
    await waitFor(() => expect(document.activeElement).toBe(dismissButton));
    fireEvent.keyDown(dismissButton, { key: "Escape" });
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
