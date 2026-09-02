import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bootstrapRuntimeSession } from "../chat/chatSessionBootstrap";
import { getHealth, getTtsProviders } from "../chat/runtimeClient";
import { useSettingsRuntime } from "./useSettingsRuntime";

vi.mock("../chat/chatSessionBootstrap", () => ({
  bootstrapRuntimeSession: vi.fn(),
}));

vi.mock("../chat/runtimeClient", () => ({
  getHealth: vi.fn(),
  getMemory: vi.fn(),
  getTtsProviders: vi.fn(),
  resetSession: vi.fn(),
  selectTtsProvider: vi.fn(),
}));

vi.mock("../chat/useChatAvatar", () => ({
  useChatAvatar: () => ({
    canvasRef: { current: null },
    avatarManifest: {
      avatarId: "avatar-lab",
      displayName: "Avatar Lab",
      rendererKind: "fake",
    },
    snapshot: {},
    rendererKind: "fallback",
    resetAvatar: vi.fn(),
  }),
}));

describe("useSettingsRuntime", () => {
  beforeEach(() => {
    vi.mocked(bootstrapRuntimeSession).mockResolvedValue({
      health: { version: "test", providers: {} },
      character: { character_id: "ayachi_nene", display_name: "绫地宁宁" },
      sessionId: "00000000-0000-4000-8000-000000000201",
    } as never);
    vi.mocked(getTtsProviders).mockResolvedValue([]);
    vi.mocked(getHealth).mockResolvedValue({
      status: "ok",
      version: "test",
      providers: { llm: "demo", tts: "fake" },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("bootstraps settings without creating media transports", async () => {
    const webSocket = vi.fn(() => {
      throw new Error("Settings must not open a WebSocket");
    });
    const audioContext = vi.fn(() => {
      throw new Error("Settings must not create an AudioContext");
    });
    vi.stubGlobal("WebSocket", webSocket);
    vi.stubGlobal("AudioContext", audioContext);

    const { result } = renderHook(() => useSettingsRuntime());
    await waitFor(() => expect(result.current.connection).toBe("connected"));

    expect(bootstrapRuntimeSession).toHaveBeenCalledOnce();
    expect(webSocket).not.toHaveBeenCalled();
    expect(audioContext).not.toHaveBeenCalled();
  });

  it("marks a once-connected settings window offline when Runtime later fails", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useSettingsRuntime());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.connection).toBe("connected");

    vi.mocked(getHealth).mockRejectedValueOnce(new Error("runtime stopped"));
    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.connection).toBe("offline");
    expect(result.current.error).toBe("runtime stopped");
    vi.useRealTimers();
  });
});
