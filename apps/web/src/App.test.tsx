import { createRef } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { ChatMessage } from "./features/chat/types";
import type {
  VoiceActivationMode,
  VoiceConnectionState,
} from "./features/chat/voiceClient";

const session = {
  canvasRef: createRef<HTMLCanvasElement>(),
  snapshot: null,
  rendererKind: "fake" as const,
  avatarWarning: null,
  touch: vi.fn(),
  health: {
    status: "ok" as const,
    version: "0.1.0",
    providers: { llm: "demo", tts: "fake", stt: "disabled" },
  },
  character: {
    character_id: "default",
    display_name: "绫地宁宁",
    tagline: "《サノバウィッチ》主题的非官方本地角色 Demo",
    greeting: "欢迎回来。那个……今天也要在这里陪我聊一会儿吗？",
    accent_color: "#d96b96",
    voice_profile: {
      voice_id: "ayachi_nene_local",
      display_name: "Qwen3-TTS 绫地宁宁本地训练音色",
      language: "zh",
      provider: "qwen3_tts_mlx",
      model: "Ayachi-Nene-Qwen3-TTS-0.6B-Pilot-8bit",
      speaker_id: 0,
      speed: 1.0,
      license: "local-evaluation-only",
    },
    content_notice: "非官方同人技术 Demo。",
  },
  sessionId: "00000000-0000-4000-8000-000000000001",
  messages: [] as ChatMessage[],
  memories: [],
  connection: "connected" as const,
  error: null,
  resetting: false,
  ttsProviders: [
    {
      provider_id: "qwen3_tts_mlx",
      display_name: "Qwen3-TTS · MLX（默认）",
      model: "Qwen3-TTS-0.6B",
      languages: ["zh", "ja", "en"],
      supports_voice_cloning: true,
      supports_style: false,
      supports_speed: false,
      supports_pitch: false,
      native_streaming: true,
      local_only: true,
      status: "ready" as const,
      model_loaded: false,
      queue_depth: 0,
      device: "mlx",
      selected: true,
    },
    {
      provider_id: "gpt_sovits",
      display_name: "GPT-SoVITS · 本地角色模型",
      model: "local-character-v2ProPlus",
      languages: ["zh", "ja", "en"],
      supports_voice_cloning: true,
      supports_style: false,
      supports_speed: false,
      supports_pitch: false,
      native_streaming: false,
      local_only: true,
      status: "ready" as const,
      model_loaded: false,
      queue_depth: 0,
      device: "cpu",
      selected: false,
    },
  ],
  ttsProviderId: "qwen3_tts_mlx",
  ttsSwitching: false,
  voiceState: "disconnected" as VoiceConnectionState,
  voiceConnected: false,
  voiceDevices: [],
  voiceDeviceId: "",
  voiceInputLevel: 0,
  voiceActivationMode: "push_to_talk" as VoiceActivationMode,
  voiceTransmitting: false,
  voiceActivity: "idle" as const,
  voiceTranscript: null,
  setVoiceDeviceId: vi.fn(),
  setVoiceActivationMode: vi.fn(),
  beginPushToTalk: vi.fn(),
  endPushToTalk: vi.fn(),
  refreshVoiceDevices: vi.fn(),
  toggleVoice: vi.fn(),
  changeTtsProvider: vi.fn(),
  send: vi.fn(),
  interruptActive: vi.fn(),
  resetAll: vi.fn().mockResolvedValue(true),
  refreshMemories: vi.fn().mockResolvedValue(undefined),
};

vi.mock("./features/chat/useChatSession", () => ({
  useChatSession: () => session,
}));

describe("ChatWaifu usable demo", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    session.voiceState = "disconnected";
    session.voiceConnected = false;
    session.voiceActivationMode = "push_to_talk";
    session.voiceTransmitting = false;
    session.messages = [];
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the connected character conversation surface", () => {
    render(<App />);

    expect(
      screen.getByRole("link", { name: "ChatWaifu NEXT home" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "绫地宁宁" })).toBeTruthy();
    expect(screen.getByText("LOCAL LINK")).toBeTruthy();
    expect(screen.getByText("fake")).toBeTruthy();
    expect(
      screen.getByRole("region", { name: "当前对话" }).textContent,
    ).toContain("欢迎回来");
    expect(screen.getByRole("button", { name: "Skills & 插件" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "记忆中心" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /LOG.*历史/ }));
    expect(
      screen.getByRole("complementary", { name: "对话历史" }),
    ).toBeTruthy();
    expect(screen.getByText("故事还没有开始。")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Avatar Lab" })).toBeNull();
    expect(screen.queryByRole("button", { name: "运行状态 Skill" })).toBeNull();
    const messageBox = screen.getByRole("textbox", { name: "Message" });
    expect(messageBox.getAttribute("placeholder")).toContain(
      "和绫地宁宁说点什么",
    );
    const sendButton = screen.getByRole("button", { name: "Send message" });
    expect(sendButton).toBeInstanceOf(HTMLButtonElement);
    if (!(sendButton instanceof HTMLButtonElement))
      throw new Error("expected send button");
    expect(sendButton.disabled).toBe(true);
  });

  it("renders the transparent desktop-pet surface on its dedicated route", () => {
    window.history.replaceState({}, "", "/desktop-pet");

    render(<App />);

    expect(screen.getByLabelText("拖动桌宠")).toBeTruthy();
    expect(screen.getByText("NENE ONLINE")).toBeTruthy();
    expect(screen.getByText(/欢迎回来/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "摸摸绫地宁宁" }));
    expect(session.touch).toHaveBeenCalledOnce();
  });

  it("lets desktop-pet users independently hide subtitles and online status", () => {
    window.history.replaceState({}, "", "/desktop-pet");

    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "桌宠显示设置" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "字幕" }));
    expect(screen.queryByText(/欢迎回来/)).toBeNull();
    expect(screen.getByText("NENE ONLINE")).toBeTruthy();

    fireEvent.click(screen.getByRole("checkbox", { name: "在线状态" }));
    expect(screen.queryByText("NENE ONLINE")).toBeNull();
    expect(screen.getByRole("button", { name: "桌宠显示设置" })).toBeTruthy();
  });

  it("keeps the desktop control center from becoming a second media owner", () => {
    window.history.replaceState({}, "", "/control-center");

    render(<App />);

    const voiceButton = screen.getByRole("button", { name: "连接麦克风" });
    expect(voiceButton).toBeInstanceOf(HTMLButtonElement);
    if (!(voiceButton instanceof HTMLButtonElement)) {
      throw new Error("expected voice button");
    }
    expect(voiceButton.disabled).toBe(true);
  });

  it("shows only the blinking caret while waiting for the first assistant token", () => {
    session.messages = [
      {
        id: "00000000-0000-4000-8000-000000000101",
        role: "assistant",
        text: "",
        generationId: "00000000-0000-4000-8000-000000000101",
        pending: true,
      },
    ];

    render(<App />);

    const dialogue = screen.getByRole("region", { name: "当前对话" });
    expect(dialogue.textContent).not.toContain("欢迎回来");
    expect(dialogue.querySelector(".typing-caret")).toBeTruthy();
  });

  it("confirms before resetting conversation and memory", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "重置对话和记忆" }));

    await waitFor(() => expect(session.resetAll).toHaveBeenCalledOnce());
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("无法撤销"));
  });

  it("defaults to intentional push-to-talk capture", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /CONFIG.*设置/ }));

    const activationMode = screen.getByRole("combobox", {
      name: "语音响应方式",
    });
    expect(activationMode).toBeInstanceOf(HTMLSelectElement);
    if (!(activationMode instanceof HTMLSelectElement)) {
      throw new Error("expected activation mode select");
    }
    expect(activationMode.value).toBe("push_to_talk");
    expect(screen.getByText("连接后按住说话，旁边聊天不会触发")).toBeTruthy();

    fireEvent.change(activationMode, {
      target: { value: "open_mic" },
    });
    expect(session.setVoiceActivationMode).toHaveBeenCalledWith("open_mic");
  });

  it("defaults to Qwen TTS and can select GPT-SoVITS", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /CONFIG.*设置/ }));

    const framing = screen.getByRole("combobox", { name: "角色构图" });
    expect(framing).toBeInstanceOf(HTMLSelectElement);
    if (!(framing instanceof HTMLSelectElement))
      throw new Error("expected avatar framing select");
    expect(framing.value).toBe("bust");
    fireEvent.change(framing, { target: { value: "full" } });
    expect(
      screen.getByRole("button", { name: "Touch avatar" }).className,
    ).toContain("framing-full");

    const tts = screen.getByRole("combobox", { name: "选择语音模型" });
    expect(tts).toBeInstanceOf(HTMLSelectElement);
    if (!(tts instanceof HTMLSelectElement))
      throw new Error("expected TTS select");
    expect(tts.value).toBe("qwen3_tts_mlx");

    fireEvent.change(tts, { target: { value: "gpt_sovits" } });
    expect(session.changeTtsProvider).toHaveBeenCalledWith("gpt_sovits");
  });

  it("only transmits while the push-to-talk control is held", () => {
    session.voiceState = "connected";
    session.voiceConnected = true;
    render(<App />);

    const pushToTalk = screen.getByRole("button", { name: "按住说话" });
    fireEvent.pointerDown(pushToTalk, { pointerId: 7 });
    expect(session.beginPushToTalk).toHaveBeenCalledOnce();

    fireEvent.pointerUp(pushToTalk, { pointerId: 7 });
    expect(session.endPushToTalk).toHaveBeenCalledOnce();
  });
});
