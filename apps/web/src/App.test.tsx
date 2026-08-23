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
    display_name: "小雾",
    tagline: "住在你设备里的本地 AI 伙伴",
    greeting: "你好呀，我是小雾。",
    accent_color: "#8b5cf6",
  },
  sessionId: "00000000-0000-4000-8000-000000000001",
  messages: [],
  memories: [],
  connection: "connected" as const,
  error: null,
  skillSummary: null,
  resetting: false,
  voiceState: "disconnected" as const,
  voiceConnected: false,
  voiceDevices: [],
  voiceDeviceId: "",
  voiceInputLevel: 0,
  voiceActivity: "idle" as const,
  voiceTranscript: null,
  setVoiceDeviceId: vi.fn(),
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
    session.resetAll.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the connected character conversation surface", () => {
    render(<App />);

    expect(screen.getByText("ChatWaifu NEXT")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "小雾" })).toBeTruthy();
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
});
