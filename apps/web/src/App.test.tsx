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
import type { SubtitlePlaybackProgress } from "./features/chat/subtitlePlayback";
import type {
  VoiceActivationMode,
  VoiceConnectionState,
} from "./features/chat/voiceClient";

const nativeWindow = vi.hoisted(() => ({
  label: String("avatar-overlay"),
  startDragging: vi.fn().mockResolvedValue(undefined),
}));

const session = {
  canvasRef: createRef<HTMLCanvasElement>(),
  snapshot: null,
  rendererKind: "fake" as const,
  avatarWarning: null as string | null,
  hitTest: vi.fn(() => [
    {
      interaction_id: "00000000-0000-4000-8000-000000000099",
      avatar_id: "ayachi-nene",
      kind: "touch" as const,
      target: "touched_head",
      metadata: { area_id: "head" },
    },
  ]),
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
  subtitlePlayback: null as SubtitlePlaybackProgress | null,
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
const defaultTtsProviders = session.ttsProviders.map((provider) => ({
  ...provider,
}));

vi.mock("./features/chat/useChatSession", () => ({
  useChatSession: () => session,
}));

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => nativeWindow,
}));

describe("ChatWaifu usable demo", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    window.localStorage?.clear();
    session.voiceState = "disconnected";
    session.voiceConnected = false;
    session.voiceActivationMode = "push_to_talk";
    session.voiceTransmitting = false;
    session.avatarWarning = null;
    session.messages = [];
    session.subtitlePlayback = null;
    session.ttsProviders = defaultTtsProviders.map((provider) => ({
      ...provider,
    }));
    session.ttsProviderId = "qwen3_tts_mlx";
    session.hitTest.mockReturnValue([
      {
        interaction_id: "00000000-0000-4000-8000-000000000099",
        avatar_id: "ayachi-nene",
        kind: "touch",
        target: "touched_head",
        metadata: { area_id: "head" },
      },
    ]);
    nativeWindow.label = "avatar-overlay";
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
    Reflect.deleteProperty(window, "__CHATWAIFU_NATIVE_SURFACE__");
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

    expect(screen.queryByText("NENE ONLINE")).toBeNull();
    expect(screen.getByText(/欢迎回来/)).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "桌宠文字消息" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "连接麦克风" }).querySelector("svg"),
    ).toBeTruthy();
    const avatar = screen.getByRole("button", { name: "摸摸绫地宁宁" });
    fireEvent.pointerDown(avatar, {
      button: 0,
      pointerId: 1,
      clientX: 160,
      clientY: 120,
    });
    fireEvent.pointerUp(avatar, {
      button: 0,
      pointerId: 1,
      clientX: 160,
      clientY: 120,
    });
    expect(session.touch).toHaveBeenCalledOnce();
  });

  it.each(["touched_head", "touched_body", "touched_avatar"])(
    "drags the native pet from %s without firing touch",
    (target) => {
      window.history.replaceState({}, "", "/desktop-pet");
      Object.defineProperty(window, "__TAURI_INTERNALS__", {
        configurable: true,
        value: {},
      });
      session.hitTest.mockReturnValue([
        {
          interaction_id: "00000000-0000-4000-8000-000000000098",
          avatar_id: "ayachi-nene",
          kind: "touch",
          target,
          metadata: { area_id: target.replace("touched_", "") },
        },
      ]);

      render(<App />);

      const avatar = screen.getByRole("button", { name: "摸摸绫地宁宁" });
      fireEvent.pointerDown(avatar, {
        button: 0,
        pointerId: 7,
        clientX: 160,
        clientY: 120,
      });
      fireEvent.pointerMove(avatar, {
        pointerId: 7,
        clientX: 172,
        clientY: 120,
      });
      expect(nativeWindow.startDragging).toHaveBeenCalledOnce();
      fireEvent.pointerUp(avatar, {
        button: 0,
        pointerId: 7,
        clientX: 172,
        clientY: 120,
      });

      expect(session.touch).not.toHaveBeenCalled();
    },
  );

  it("does not drag or touch through the transparent avatar background", () => {
    window.history.replaceState({}, "", "/desktop-pet");
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
    session.hitTest.mockReturnValue([]);

    render(<App />);

    const avatar = screen.getByRole("button", { name: "摸摸绫地宁宁" });
    fireEvent.pointerDown(avatar, {
      button: 0,
      pointerId: 8,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerMove(avatar, {
      pointerId: 8,
      clientX: 40,
      clientY: 20,
    });
    fireEvent.pointerUp(avatar, {
      button: 0,
      pointerId: 8,
      clientX: 40,
      clientY: 20,
    });

    expect(nativeWindow.startDragging).not.toHaveBeenCalled();
    expect(session.touch).not.toHaveBeenCalled();
  });

  it("shows the specific Live2D failure instead of hiding it behind fallback copy", () => {
    window.history.replaceState({}, "", "/desktop-pet");
    session.avatarWarning =
      "Cannot initialize WebGL2 with the current Windows graphics adapter.";

    render(<App />);

    expect(screen.getByRole("status").textContent).toBe(
      "Cannot initialize WebGL2 with the current Windows graphics adapter.",
    );
    expect(screen.queryByText("Live2D 未就绪，已使用安全回退。")).toBeNull();
  });

  it("lets an unsupported desktop WebView explain why voice is unavailable", () => {
    window.history.replaceState({}, "", "/desktop-pet");
    session.voiceState = "unsupported";

    render(<App />);

    const voiceButton = screen.getByRole("button", {
      name: "检查麦克风不可用原因",
    });
    expect((voiceButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(voiceButton);
    expect(session.toggleVoice).toHaveBeenCalledOnce();
  });

  it("shows only the caret before the first desktop-pet reply token", () => {
    window.history.replaceState({}, "", "/desktop-pet");
    session.messages = [
      {
        id: "generation-pending",
        role: "assistant",
        text: "",
        generationId: "generation-pending",
        pending: true,
      },
    ];

    render(<App />);

    expect(screen.queryByText(/欢迎回来/)).toBeNull();
    expect(
      document.querySelector(".desktop-pet-dialogue .typing-caret"),
    ).toBeTruthy();
  });

  it("folds blank lines and flips whole subtitle lines with audio progress", () => {
    window.history.replaceState({}, "", "/desktop-pet");
    session.messages = [
      {
        id: "generation-streaming",
        role: "assistant",
        text: "一二三四\n\n五六七八\n九十甲乙\n丙丁戊己",
        generationId: "generation-streaming",
        pending: true,
      },
    ];
    const { rerender } = render(<App />);
    const dialogue = document.querySelector(".desktop-pet-dialogue p");
    if (!(dialogue instanceof HTMLParagraphElement))
      throw new Error("expected desktop-pet dialogue");
    Object.defineProperty(dialogue, "scrollHeight", {
      configurable: true,
      value: 240,
    });
    Object.defineProperty(dialogue, "clientHeight", {
      configurable: true,
      value: 60,
    });
    expect(dialogue.textContent).not.toContain("\n\n");
    session.subtitlePlayback = {
      generationId: "generation-streaming",
      segmentIndex: 0,
      playedTextUnits: 8,
      phase: "playing",
    };
    rerender(<App />);

    expect(dialogue.scrollTop).toBe(80);

    session.subtitlePlayback = {
      ...session.subtitlePlayback,
      playedTextUnits: 8.5,
    };
    rerender(<App />);
    expect(dialogue.scrollTop).toBe(80);

    session.subtitlePlayback = {
      ...session.subtitlePlayback,
      playedTextUnits: 10,
    };
    rerender(<App />);
    expect(dialogue.scrollTop).toBe(100);
  });

  it("sends typed messages from the desktop-pet hover composer", async () => {
    window.history.replaceState({}, "", "/desktop-pet");
    session.send.mockResolvedValueOnce(undefined);

    render(<App />);

    const messageBox = screen.getByRole("textbox", {
      name: "桌宠文字消息",
    });
    const petShell = document.querySelector(".desktop-pet-shell");
    fireEvent.change(messageBox, { target: { value: "今天一起学 Python 吧" } });
    expect(petShell?.getAttribute("data-actions-active")).toBe("true");
    fireEvent.submit(messageBox.closest("form")!);

    await waitFor(() => {
      expect(session.send).toHaveBeenCalledWith("今天一起学 Python 吧");
    });
    expect((messageBox as HTMLInputElement).value).toBe("");
  });

  it("reveals desktop-pet controls from pointer presence without focus", () => {
    window.history.replaceState({}, "", "/desktop-pet");

    render(<App />);

    const petShell = document.querySelector(".desktop-pet-shell");
    if (!(petShell instanceof HTMLElement))
      throw new Error("expected desktop-pet shell");
    expect(petShell.getAttribute("data-pointer-inside")).toBe("false");
    fireEvent.pointerEnter(petShell);
    expect(petShell.getAttribute("data-pointer-inside")).toBe("true");
    expect(document.activeElement).toBe(document.body);
    fireEvent.pointerLeave(petShell);
    expect(petShell.getAttribute("data-pointer-inside")).toBe("false");
  });

  it("keeps the compact HUD limited to subtitle visibility", () => {
    window.history.replaceState({}, "", "/desktop-pet");

    render(<App />);

    const displaySettings = screen.getByRole("button", {
      name: "桌宠显示设置",
    });
    const petShell = document.querySelector(".desktop-pet-shell");
    expect(petShell?.getAttribute("data-actions-active")).toBe("false");
    fireEvent.click(displaySettings);
    expect(petShell?.getAttribute("data-actions-active")).toBe("true");
    expect(screen.queryByText("NENE ONLINE")).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "在线状态" })).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "字幕" }));
    expect(screen.queryByText(/欢迎回来/)).toBeNull();
    expect(screen.getByRole("button", { name: "桌宠显示设置" })).toBeTruthy();
  });

  it("renders a dedicated desktop settings window without conversation controls", async () => {
    window.history.replaceState({}, "", "/desktop-settings");

    render(<App />);

    expect(
      screen.getByRole("heading", { level: 1, name: "桌宠" }),
    ).toBeTruthy();
    const settingsNavigation = screen.getByRole("navigation", {
      name: "设置分类",
    });
    expect(settingsNavigation).toBeTruthy();
    expect(settingsNavigation.querySelectorAll("button svg")).toHaveLength(5);
    expect(
      document.querySelector(".desktop-settings-app-icon svg"),
    ).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Conversation" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "显示在线状态" })).toBeNull();

    const subtitle = await screen.findByRole("switch", { name: "显示字幕" });
    expect(subtitle).toBeInstanceOf(HTMLInputElement);
    if (!(subtitle instanceof HTMLInputElement))
      throw new Error("expected subtitle switch");
    await waitFor(() => expect(subtitle.disabled).toBe(false));
    fireEvent.click(subtitle);
    expect(subtitle.checked).toBe(false);
  });

  it("uses the native control-center role when Windows loses the settings path", () => {
    window.history.replaceState({}, "", "/desktop-pet");
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
    nativeWindow.label = "control-center";

    render(<App />);

    expect(
      screen.getByRole("heading", { level: 1, name: "桌宠" }),
    ).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "设置分类" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "摸摸绫地宁宁" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "桌宠文字消息" })).toBeNull();
  });

  it("uses the host-injected settings surface when Windows reports stale pet context", () => {
    window.history.replaceState({}, "", "/desktop-pet");
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
    Object.defineProperty(window, "__CHATWAIFU_NATIVE_SURFACE__", {
      configurable: true,
      value: "desktop-settings",
    });
    nativeWindow.label = "avatar-overlay";

    render(<App />);

    expect(
      screen.getByRole("heading", { level: 1, name: "桌宠" }),
    ).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "设置分类" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "摸摸绫地宁宁" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "桌宠文字消息" })).toBeNull();
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

  it("shows Bailian once and resolves it to the preferred concrete API", () => {
    session.ttsProviders.push(
      {
        ...defaultTtsProviders[0],
        provider_id: "aliyun_qwen_realtime",
        display_name: "阿里云百炼 · Qwen3-TTS VC",
        model: "qwen3-tts-vc-realtime-2026-01-15",
        local_only: false,
        selected: false,
      },
      {
        ...defaultTtsProviders[0],
        provider_id: "aliyun_cosyvoice_realtime",
        display_name: "阿里云百炼 · CosyVoice",
        model: "cosyvoice-v3.5-plus",
        local_only: false,
        selected: false,
      },
    );
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /CONFIG.*设置/ }));

    const tts = screen.getByRole("combobox", { name: "选择语音模型" });
    expect(screen.getAllByRole("option", { name: "阿里云百炼" })).toHaveLength(
      1,
    );
    fireEvent.change(tts, { target: { value: "aliyun_bailian" } });
    expect(session.changeTtsProvider).toHaveBeenCalledWith(
      "aliyun_cosyvoice_realtime",
    );
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
