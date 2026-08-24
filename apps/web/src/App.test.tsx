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
      voice_id: "ayachi-nene-demo-zh",
      display_name: "宁宁 Demo 合成女声",
      language: "zh",
      provider: "sherpa_kokoro_worker",
      model: "kokoro-multi-lang-v1_1",
      speaker_id: 3,
      speed: 1.04,
      license: "Apache-2.0",
    },
    content_notice: "非官方同人技术 Demo。",
  },
  sessionId: "00000000-0000-4000-8000-000000000001",
  messages: [],
  memories: [],
  connection: "connected" as const,
  error: null,
  skillSummary: null,
  resetting: false,
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
  send: vi.fn(),
  interruptActive: vi.fn(),
  checkStatus: vi.fn(),
  resetAll: vi.fn().mockResolvedValue(true),
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
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the connected character conversation surface", () => {
    render(<App />);

    expect(screen.getByText("ChatWaifu NEXT")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "绫地宁宁" })).toBeTruthy();
    expect(screen.getByText("Runtime online")).toBeTruthy();
    expect(screen.getByText("LLM · demo")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Message" })).toBeTruthy();
    const sendButton = screen.getByRole("button", { name: "Send message" });
    expect(sendButton).toBeInstanceOf(HTMLButtonElement);
    if (!(sendButton instanceof HTMLButtonElement))
      throw new Error("expected send button");
    expect(sendButton.disabled).toBe(true);
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
