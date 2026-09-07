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

const native = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: native.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: native.listen }));

describe("native settings restart", () => {
  type Status = import("../chat/runtimeEndpoint").DesktopRuntimeStatus;
  let listener: (event: { payload: Status }) => void;
  let stop: ReturnType<typeof vi.fn<() => void>>;
  const ready: Status = {
    state: "ready",
    runtime_url: "http://127.0.0.1:1111",
    token: "old",
    restart_count: 0,
    workers: [],
  };
  const core = {
    health: { version: "test", providers: {} },
    character: { character_id: "ayachi_nene", display_name: "绫地宁宁" },
    sessionId: "00000000-0000-4000-8000-000000000201",
  };
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
    stop = vi.fn();
    native.listen.mockImplementation((_name, callback) => {
      listener = callback;
      return Promise.resolve(stop);
    });
    native.invoke.mockResolvedValue(ready);
    vi.mocked(bootstrapRuntimeSession).mockResolvedValue(core as never);
    vi.mocked(getTtsProviders).mockResolvedValue([]);
  });
  afterEach(() => {
    cleanup();
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
    vi.useRealTimers();
    vi.restoreAllMocks();
  });
  async function emit(status: Status) {
    await act(async () => {
      listener({ payload: status });
    });
  }
  it("follows starting, circuit open and ready with the new endpoint and clears errors", async () => {
    const { resolveRuntimeConnection } =
      await import("../chat/runtimeEndpoint");
    const { result, unmount } = renderHook(() => useSettingsRuntime());
    await waitFor(() => expect(result.current.connection).toBe("connected"));
    const oldSignal = vi.mocked(bootstrapRuntimeSession).mock.calls[0][1];
    await emit({ ...ready, state: "starting" });
    expect(result.current.connection).toBe("connecting");
    expect(result.current.sessionId).toBeNull();
    expect(oldSignal?.aborted).toBe(true);
    await emit({ ...ready, state: "circuit_open", detail: "worker failed" });
    expect(result.current.error).toBe("worker failed");
    await emit({
      ...ready,
      runtime_url: "http://127.0.0.1:2222",
      token: "new",
      restart_count: 1,
    });
    expect(result.current.connection).toBe("connected");
    expect(result.current.error).toBeNull();
    expect(await resolveRuntimeConnection()).toEqual({
      baseUrl: "http://127.0.0.1:2222",
      token: "new",
      restartCount: 1,
    });
    expect(bootstrapRuntimeSession).toHaveBeenCalledTimes(2);
    unmount();
    expect(stop).toHaveBeenCalledOnce();
    const second = renderHook(() => useSettingsRuntime());
    await waitFor(() =>
      expect(second.result.current.connection).toBe("connected"),
    );
    expect(native.listen).toHaveBeenCalledTimes(2);
  });
  it("ignores an old bootstrap that completes after a newer boot", async () => {
    let finish!: (value: never) => void;
    vi.mocked(bootstrapRuntimeSession).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result } = renderHook(() => useSettingsRuntime());
    await waitFor(() => expect(bootstrapRuntimeSession).toHaveBeenCalledOnce());
    await emit({ ...ready, state: "starting" });
    await emit({ ...ready, restart_count: 1 });
    expect(result.current.connection).toBe("connected");
    await act(async () => {
      finish({ ...core, sessionId: "stale" } as never);
    });
    expect(result.current.sessionId).toBe(core.sessionId);
    expect(getTtsProviders).toHaveBeenCalledOnce();
  });
  it("ignores stale health failure after reconnect", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useSettingsRuntime());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.connection).toBe("connected");
    let fail!: (error: Error) => void;
    vi.mocked(getHealth).mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          fail = reject;
        }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await emit({ ...ready, state: "backoff" });
    await emit({ ...ready, restart_count: 1 });
    await act(async () => {
      fail(new Error("stale health failure"));
    });
    expect(result.current.connection).toBe("connected");
    expect(result.current.error).toBeNull();
  });
  it("does not replay a mutation or publish its late result after restart", async () => {
    const { selectTtsProvider } = await import("../chat/runtimeClient");
    let finish!: (value: never) => void;
    vi.mocked(selectTtsProvider).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result } = renderHook(() => useSettingsRuntime());
    await waitFor(() => expect(result.current.connection).toBe("connected"));
    let mutation!: Promise<void>;
    act(() => {
      mutation = result.current.changeTtsProvider("old-provider");
    });
    await emit({ ...ready, state: "starting" });
    await emit({ ...ready, restart_count: 1 });
    await act(async () => {
      finish({ provider_id: "old-provider" } as never);
      await mutation;
    });
    expect(selectTtsProvider).toHaveBeenCalledOnce();
    expect(result.current.ttsProviderId).toBe("");
    expect(result.current.ttsSwitching).toBe(false);
  });
  it("cleans a listener whose registration finishes after window closure", async () => {
    let finish!: (value: () => void) => void;
    native.listen.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { unmount } = renderHook(() => useSettingsRuntime());
    await waitFor(() => expect(native.listen).toHaveBeenCalledOnce());
    unmount();
    await act(async () => {
      finish(stop);
    });
    expect(stop).toHaveBeenCalledOnce();
    expect(native.invoke).not.toHaveBeenCalled();
  });
});
