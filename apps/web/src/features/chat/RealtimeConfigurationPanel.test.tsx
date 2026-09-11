import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RealtimeConfigurationPanel } from "./RealtimeConfigurationPanel";
import * as realtimeClient from "./runtime-client/realtimeClient";
import type { RealtimeConfigurationSnapshot } from "./types";
import * as runtimeEndpoint from "./runtimeEndpoint";

vi.mock(import("./runtimeEndpoint"), async (importOriginal) => ({
  ...(await importOriginal()),
  isDesktopHost: vi.fn(() => false),
  observeDesktopRuntime: vi.fn(),
}));

vi.mock(import("./runtime-client/realtimeClient"), async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getRealtimeConfiguration: vi.fn(),
    updateRealtimeConfiguration: vi.fn(),
  };
});

function flushUpdate(update: () => void) {
  return act(() => {
    update();
    return Promise.resolve();
  });
}

const sampleSnapshot: RealtimeConfigurationSnapshot = {
  schema_version: "1.0",
  revision: 10,
  connection_mode: "cascade",
  cloud_backend: "openai",
  model: "",
  voice: "marin",
  transcription_model: "gpt-4o-mini-transcribe",
  cloud_tools_enabled: false,
  cloud_egress_consent: false,
  api_key_configured: false,
  active_connections: 0,
};

describe("RealtimeConfigurationPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(runtimeEndpoint.isDesktopHost).mockReturnValue(false);
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValue({
      ...sampleSnapshot,
    });
    vi.mocked(realtimeClient.updateRealtimeConfiguration).mockImplementation(
      (update) =>
        Promise.resolve({
          schema_version: "1.0",
          revision: update.expected_revision + 1,
          connection_mode: update.connection_mode,
          cloud_backend: "openai",
          model: update.model,
          voice: update.voice,
          transcription_model: update.transcription_model,
          cloud_tools_enabled: update.cloud_tools_enabled,
          cloud_egress_consent: update.cloud_egress_consent,
          api_key_configured: update.clear_api_key
            ? false
            : Boolean(update.api_key),
          active_connections: 0,
        }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loadredactedconfiguredkeyblank: loads redacted configured key with blank input and configured status", async () => {
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      api_key_configured: true,
    });

    render(<RealtimeConfigurationPanel />);

    expect(
      await screen.findByRole("region", { name: "实时语音设置" }),
    ).toBeTruthy();

    const keyInput = screen.getByLabelText<HTMLInputElement>("OpenAI API Key");
    expect(keyInput.value).toBe("");
    expect(screen.getByText("· 已配置")).toBeTruthy();
    expect(keyInput.placeholder).toBe("留空保持原密钥");
  });

  it("blankkeepsrequest: leaving blank key keeps old key in save payload", async () => {
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      api_key_configured: true,
      revision: 12,
    });

    render(<RealtimeConfigurationPanel />);

    await screen.findByRole("region", { name: "实时语音设置" });

    // User does not type any new API key, clear checkbox is not checked
    const saveButton = screen.getByRole("button", { name: "保存配置" });
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(realtimeClient.updateRealtimeConfiguration).toHaveBeenCalledOnce(),
    );

    expect(realtimeClient.updateRealtimeConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 12,
        api_key: null,
        clear_api_key: false,
      }),
      expect.any(AbortSignal),
    );
  });

  it("clear: explicit clear checkbox sends clear_api_key: true and api_key: null", async () => {
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      api_key_configured: true,
      revision: 15,
    });

    render(<RealtimeConfigurationPanel />);

    await screen.findByRole("region", { name: "实时语音设置" });

    const clearCheckbox = screen.getByLabelText("清除已配置的 API Key");
    fireEvent.click(clearCheckbox);

    const saveButton = screen.getByRole("button", { name: "保存配置" });
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(realtimeClient.updateRealtimeConfiguration).toHaveBeenCalledOnce(),
    );

    expect(realtimeClient.updateRealtimeConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 15,
        clear_api_key: true,
        api_key: null,
      }),
      expect.any(AbortSignal),
    );
  });

  it("fields+consent serialization expected_revision: serializes all inputs and consent with expected revision", async () => {
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      revision: 20,
      connection_mode: "cascade",
      model: "",
      voice: "marin",
      transcription_model: "gpt-4o-mini-transcribe",
      cloud_tools_enabled: false,
      cloud_egress_consent: false,
    });

    render(<RealtimeConfigurationPanel />);

    await screen.findByRole("region", { name: "实时语音设置" });

    // Change connection mode
    fireEvent.change(screen.getByLabelText("连接模式"), {
      target: { value: "cloud_realtime" },
    });

    // Change model (exact model, no invented default)
    fireEvent.change(screen.getByLabelText("Realtime 模型"), {
      target: { value: "gpt-4o-realtime-preview-2026" },
    });

    // Change voice
    fireEvent.change(screen.getByLabelText("Realtime 音色"), {
      target: { value: "verse" },
    });

    // Change transcription model
    fireEvent.change(screen.getByLabelText("转写模型"), {
      target: { value: "whisper-large-v3" },
    });

    // Toggle tools
    fireEvent.click(screen.getByLabelText("允许云端只读工具调用"));

    // Check consent
    fireEvent.click(screen.getByLabelText("云端数据传输授权"));

    // Save
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() =>
      expect(realtimeClient.updateRealtimeConfiguration).toHaveBeenCalledOnce(),
    );

    expect(realtimeClient.updateRealtimeConfiguration).toHaveBeenCalledWith(
      {
        schema_version: "1.0",
        expected_revision: 20,
        connection_mode: "cloud_realtime",
        cloud_backend: "openai",
        model: "gpt-4o-realtime-preview-2026",
        voice: "verse",
        transcription_model: "whisper-large-v3",
        cloud_tools_enabled: true,
        cloud_egress_consent: true,
        api_key: null,
        clear_api_key: false,
      },
      expect.any(AbortSignal),
    );
  });

  it("pending disable: disables controls and save button while save is in flight", async () => {
    let finishSave!: (value: RealtimeConfigurationSnapshot) => void;
    vi.mocked(
      realtimeClient.updateRealtimeConfiguration,
    ).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishSave = resolve;
        }),
    );

    render(<RealtimeConfigurationPanel />);

    await screen.findByRole("region", { name: "实时语音设置" });

    const saveButton = screen.getByRole("button", { name: "保存配置" });
    const modelInput = screen.getByLabelText("Realtime 模型");
    const modeSelect = screen.getByLabelText("连接模式");

    fireEvent.click(saveButton);

    await waitFor(() =>
      expect((saveButton as HTMLButtonElement).disabled).toBe(true),
    );
    expect((modelInput as HTMLInputElement).disabled).toBe(true);
    expect((modeSelect as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByText("正在保存…")).toBeTruthy();

    finishSave({
      ...sampleSnapshot,
      revision: 11,
    });

    await waitFor(() =>
      expect((saveButton as HTMLButtonElement).disabled).toBe(false),
    );
    expect((modelInput as HTMLInputElement).disabled).toBe(false);
    expect((modeSelect as HTMLSelectElement).disabled).toBe(false);
    expect(screen.getByText("实时语音配置已保存。")).toBeTruthy();
  });

  it("savefailretainsinput: retains user input when save fails", async () => {
    vi.mocked(realtimeClient.updateRealtimeConfiguration).mockRejectedValueOnce(
      new Error("网络连接失败，请稍后重试"),
    );

    render(<RealtimeConfigurationPanel />);

    await screen.findByRole("region", { name: "实时语音设置" });

    const modelInput = screen.getByLabelText<HTMLInputElement>("Realtime 模型");
    fireEvent.change(modelInput, { target: { value: "my-unsaved-model" } });

    const keyInput = screen.getByLabelText<HTMLInputElement>("OpenAI API Key");
    fireEvent.change(keyInput, { target: { value: "sk-my-secret-key" } });

    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(await screen.findByText("网络连接失败，请稍后重试")).toBeTruthy();

    // User inputs are retained in the form
    expect(modelInput.value).toBe("my-unsaved-model");
    expect(keyInput.value).toBe("sk-my-secret-key");
  });

  it("successclearssecret: clears secret input after save success", async () => {
    vi.mocked(realtimeClient.updateRealtimeConfiguration).mockResolvedValueOnce(
      {
        ...sampleSnapshot,
        revision: 11,
        api_key_configured: true,
      },
    );

    render(<RealtimeConfigurationPanel />);

    await screen.findByRole("region", { name: "实时语音设置" });

    const keyInput = screen.getByLabelText<HTMLInputElement>("OpenAI API Key");
    fireEvent.change(keyInput, { target: { value: "sk-ephemeral-key" } });
    expect(keyInput.value).toBe("sk-ephemeral-key");

    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(await screen.findByText("实时语音配置已保存。")).toBeTruthy();

    // Secret must be cleared from state
    expect(keyInput.value).toBe("");
    // Newly returned status is configured
    expect(screen.getByText("· 已配置")).toBeTruthy();
  });

  it("409conflictreload: displays conflict notice, preserves user input, and reloads on user action", async () => {
    vi.mocked(realtimeClient.updateRealtimeConfiguration).mockRejectedValueOnce(
      new Error("Runtime request failed (409)"),
    );

    render(<RealtimeConfigurationPanel />);

    await screen.findByRole("region", { name: "实时语音设置" });

    const modelInput = screen.getByLabelText<HTMLInputElement>("Realtime 模型");
    fireEvent.change(modelInput, {
      target: { value: "local-conflicted-edit" },
    });

    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(
      await screen.findByText(
        "配置已被其他客户端修改 (409 Conflict)，请重新加载最新配置。",
      ),
    ).toBeTruthy();

    // User edit is preserved before reload
    expect(modelInput.value).toBe("local-conflicted-edit");

    const reloadButton = screen.getByRole("button", {
      name: "重新加载最新配置",
    });
    expect(reloadButton).toBeTruthy();

    // Mock GET response on reload
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      revision: 25,
      model: "remote-updated-model",
    });

    fireEvent.click(reloadButton);

    await waitFor(() => expect(modelInput.value).toBe("remote-updated-model"));

    // Conflict box should be dismissed
    expect(
      screen.queryByText(
        "配置已被其他客户端修改 (409 Conflict)，请重新加载最新配置。",
      ),
    ).toBeNull();
  });

  it("runtimeendpoint changes dropstaleGET/save andsecretstate: drops stale responses and resets secret state on endpoint switch", async () => {
    let finishFirstGet!: (value: RealtimeConfigurationSnapshot) => void;
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishFirstGet = resolve;
        }),
    );

    const { rerender } = render(
      <RealtimeConfigurationPanel endpointKey="endpoint-1" />,
    );

    // Finish first load
    finishFirstGet({
      ...sampleSnapshot,
      revision: 1,
      model: "model-endpoint-1",
    });

    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("Realtime 模型").value,
      ).toBe("model-endpoint-1"),
    );

    // User enters an ephemeral secret key on endpoint 1
    const keyInput = screen.getByLabelText<HTMLInputElement>("OpenAI API Key");
    fireEvent.change(keyInput, { target: { value: "secret-for-endpoint-1" } });
    expect(keyInput.value).toBe("secret-for-endpoint-1");

    // Endpoint switches to endpoint-2 while GET is in flight for endpoint 2
    let finishSecondGet!: (value: RealtimeConfigurationSnapshot) => void;
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishSecondGet = resolve;
        }),
    );

    rerender(<RealtimeConfigurationPanel endpointKey="endpoint-2" />);

    // Secret must be immediately cleared on endpoint switch
    expect(keyInput.value).toBe("");

    // Resolve second GET
    finishSecondGet({
      ...sampleSnapshot,
      revision: 50,
      model: "model-endpoint-2",
    });

    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("Realtime 模型").value,
      ).toBe("model-endpoint-2"),
    );
  });

  it("recovers from endpoint change during save and ignores the old save", async () => {
    let finishSave!: (value: RealtimeConfigurationSnapshot) => void;
    vi.mocked(
      realtimeClient.updateRealtimeConfiguration,
    ).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishSave = resolve;
        }),
    );
    const { rerender } = render(
      <RealtimeConfigurationPanel endpointKey="one" />,
    );
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" })
          .disabled,
      ).toBe(false),
    );
    fireEvent.change(screen.getByLabelText("OpenAI API Key"), {
      target: { value: "test-only-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await screen.findByText("正在保存…");
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      revision: 40,
      model: "new-endpoint",
    });
    rerender(<RealtimeConfigurationPanel endpointKey="two" />);
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("Realtime 模型").value,
      ).toBe("new-endpoint"),
    );
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" })
        .disabled,
    ).toBe(false);
    expect(
      screen.getByLabelText<HTMLInputElement>("OpenAI API Key").value,
    ).toBe("");
    await flushUpdate(() =>
      finishSave({ ...sampleSnapshot, revision: 11, model: "old-save" }),
    );
    expect(screen.getByLabelText<HTMLInputElement>("Realtime 模型").value).toBe(
      "new-endpoint",
    );
    expect(screen.queryByText("实时语音配置已保存。")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() =>
      expect(
        realtimeClient.updateRealtimeConfiguration,
      ).toHaveBeenLastCalledWith(
        expect.objectContaining({ expected_revision: 40, api_key: null }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("does not save the old endpoint profile when the new endpoint cannot load", async () => {
    const { rerender } = render(
      <RealtimeConfigurationPanel endpointKey="one" />,
    );
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" })
          .disabled,
      ).toBe(false),
    );
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockRejectedValueOnce(
      new Error("暂时离线"),
    );
    rerender(<RealtimeConfigurationPanel endpointKey="two" />);
    await screen.findByText("暂时离线");
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" })
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "重新读取配置" }));
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" })
          .disabled,
      ).toBe(false),
    );
  });

  it("invalidates pending saves as soon as native Runtime starts restarting", async () => {
    vi.mocked(runtimeEndpoint.isDesktopHost).mockReturnValue(true);
    let emit!: (status: runtimeEndpoint.DesktopRuntimeStatus) => void;
    vi.mocked(runtimeEndpoint.observeDesktopRuntime).mockImplementation(
      (callback) => {
        emit = callback;
        return Promise.resolve();
      },
    );
    let finishSave!: (value: RealtimeConfigurationSnapshot) => void;
    vi.mocked(
      realtimeClient.updateRealtimeConfiguration,
    ).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishSave = resolve;
        }),
    );
    render(<RealtimeConfigurationPanel />);
    await flushUpdate(() =>
      emit({
        state: "ready",
        workers: [],
        restart_count: 0,
        runtime_url: "http://127.0.0.1:8813",
        token: "test",
      }),
    );
    fireEvent.change(screen.getByLabelText("OpenAI API Key"), {
      target: { value: "test-only-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await screen.findByText("正在保存…");
    await flushUpdate(() =>
      emit({ state: "starting", workers: [], restart_count: 1 }),
    );
    expect(
      screen.getByLabelText<HTMLInputElement>("OpenAI API Key").value,
    ).toBe("");
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" })
        .disabled,
    ).toBe(true);
    await flushUpdate(() =>
      finishSave({
        ...sampleSnapshot,
        model: "stale",
        api_key_configured: true,
      }),
    );
    expect(screen.queryByText("实时语音配置已保存。")).toBeNull();
    expect(realtimeClient.getRealtimeConfiguration).toHaveBeenCalledTimes(1);
    await flushUpdate(() =>
      emit({
        state: "ready",
        workers: [],
        restart_count: 1,
        runtime_url: "http://127.0.0.1:8814",
        token: "new-test",
      }),
    );
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" })
        .disabled,
    ).toBe(false);
    expect(realtimeClient.getRealtimeConfiguration).toHaveBeenCalledTimes(2);
  });

  it("does not overwrite user unsaved edits with late GET", async () => {
    let finishGet!: (value: RealtimeConfigurationSnapshot) => void;
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishGet = resolve;
        }),
    );

    render(<RealtimeConfigurationPanel />);

    const input = screen.getByLabelText<HTMLInputElement>("Realtime 模型");
    fireEvent.change(input, { target: { value: "user-unsaved-edit" } });
    expect(input.value).toBe("user-unsaved-edit");

    // The late GET resolves
    finishGet({
      ...sampleSnapshot,
      revision: 1,
      model: "server-model-from-late-get",
    });

    await waitFor(() => {
      expect(input.value).toBe("user-unsaved-edit");
    });
  });

  it("rejects saving when nonblank key and clear key are both specified", async () => {
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      api_key_configured: true,
    });

    render(<RealtimeConfigurationPanel />);

    await waitFor(() => expect(screen.queryByText("· 已配置")).toBeTruthy());

    // Check clear key
    const clearCheckbox = screen.getByLabelText("清除已配置的 API Key");
    fireEvent.click(clearCheckbox);

    // User types key
    const keyInput = screen.getByLabelText("OpenAI API Key");
    fireEvent.change(keyInput, { target: { value: "new-key" } });

    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(
      await screen.findByText("不能同时输入新密钥并勾选清除密钥。"),
    ).toBeTruthy();

    expect(realtimeClient.updateRealtimeConfiguration).not.toHaveBeenCalled();
  });

  it("displays fake backend note, active connections notice, and incomplete requirements", async () => {
    vi.mocked(realtimeClient.getRealtimeConfiguration).mockResolvedValueOnce({
      ...sampleSnapshot,
      cloud_backend: "fake",
      connection_mode: "cloud_realtime",
      model: "",
      api_key_configured: false,
      cloud_egress_consent: false,
      active_connections: 2,
    });

    render(<RealtimeConfigurationPanel />);

    await waitFor(() =>
      expect(
        screen.getByText(
          "当前有 2 个正在进行的语音连接（修改将在下次连接时生效）。",
        ),
      ).toBeTruthy(),
    );

    expect(screen.getAllByText("测试模拟后端 (fake)").length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getByText(
        "当前环境使用测试模拟后端 (fake)。保存配置时将更新为 OpenAI 后端。",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "云端实时语音配置未完成（缺少模型、API Key、云端传输授权）。仍可保存配置，但在发起语音连接时将阻止接入。",
      ),
    ).toBeTruthy();
  });
});
