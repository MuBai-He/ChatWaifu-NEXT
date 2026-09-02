import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acquireWsTicket,
  DESKTOP_RUNTIME_RESOLUTION_TIMEOUT_MS,
  resolveRuntimeUrl,
  runtimeFetchWithConnection,
  runtimeWebSocketUrlFromConnection,
  type DesktopRuntimeStatus,
  type RuntimeConnection,
} from "./runtimeEndpoint";

const nativeMocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
  statusListener: null as ((event: { payload: unknown }) => void) | null,
  unlisten: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: nativeMocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: nativeMocks.listen }));

describe("desktop Runtime endpoint", () => {
  const starting: DesktopRuntimeStatus = {
    state: "starting",
    workers: [],
    restart_count: 0,
  };

  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
    nativeMocks.statusListener = null;
    nativeMocks.invoke.mockImplementation((command: string) => {
      if (command === "get_runtime_status" || command === "start_runtime") {
        return Promise.resolve(starting);
      }
      return Promise.reject(new Error(`unexpected native command: ${command}`));
    });
    nativeMocks.listen.mockImplementation(
      (
        _event: string,
        listener: (event: { payload: DesktopRuntimeStatus }) => void,
      ) => {
        nativeMocks.statusListener = (event) =>
          listener({ payload: event.payload as DesktopRuntimeStatus });
        return Promise.resolve(nativeMocks.unlisten);
      },
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
  });

  it("waits beyond the native Worker, Runtime, and supervisor startup budgets", () => {
    const nativeStartupBudgetMs = 300_000 + 120_000 + 30_000;

    expect(DESKTOP_RUNTIME_RESOLUTION_TIMEOUT_MS).toBeGreaterThan(
      nativeStartupBudgetMs,
    );
  });

  it("accepts a healthy CUDA cold start after the former 125 second cutoff", async () => {
    let settled = false;
    const resolution = resolveRuntimeUrl(true).finally(() => {
      settled = true;
    });
    await vi.waitFor(() => expect(nativeMocks.statusListener).not.toBeNull());

    await vi.advanceTimersByTimeAsync(151_000);
    expect(settled).toBe(false);

    nativeMocks.statusListener?.({
      payload: {
        state: "ready",
        runtime_url: "http://127.0.0.1:1752",
        pid: 36_476,
        workers: ["faster-whisper", "qwen3-tts"],
        restart_count: 0,
      } satisfies DesktopRuntimeStatus,
    });

    await expect(resolution).resolves.toBe("http://127.0.0.1:1752");
    expect(nativeMocks.unlisten).toHaveBeenCalledOnce();
  });

  it("removes a listener that reports ready before its registration promise settles", async () => {
    let completeRegistration: ((unlisten: () => void) => void) | undefined;
    nativeMocks.listen.mockImplementation(
      (
        _event: string,
        listener: (event: { payload: DesktopRuntimeStatus }) => void,
      ) => {
        nativeMocks.statusListener = (event) =>
          listener({ payload: event.payload as DesktopRuntimeStatus });
        return new Promise<() => void>((resolve) => {
          completeRegistration = resolve;
        });
      },
    );

    const resolution = resolveRuntimeUrl(true);
    await vi.waitFor(() => expect(nativeMocks.statusListener).not.toBeNull());
    nativeMocks.statusListener?.({
      payload: {
        state: "ready",
        runtime_url: "http://127.0.0.1:1752",
        pid: 36_476,
        workers: ["faster-whisper", "qwen3-tts"],
        restart_count: 0,
      } satisfies DesktopRuntimeStatus,
    });
    await expect(resolution).resolves.toBe("http://127.0.0.1:1752");

    completeRegistration?.(nativeMocks.unlisten);
    await vi.waitFor(() => expect(nativeMocks.unlisten).toHaveBeenCalledOnce());
    expect(nativeMocks.invoke).not.toHaveBeenCalledWith("start_runtime");
  });
});

describe("acquireWsTicket", () => {
  it("requests ticket with purpose=events and optional session_id", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ticket: "ticket-123", purpose: "events" }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    const connection: RuntimeConnection = {
      baseUrl: "http://127.0.0.1:8765",
      token: "test-token",
    };
    const ticket = await acquireWsTicket(
      "events",
      connection,
      "session-uuid-1",
    );
    expect(ticket).toBe("ticket-123");
    expect(fetchSpy).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/v1/runtime/ws-ticket?purpose=events&session_id=session-uuid-1",
      expect.objectContaining({ method: "POST" }),
    );
    fetchSpy.mockRestore();
  });

  it("requests ticket with explicit purpose=audio", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ticket: "audio-ticket-456", purpose: "audio" }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    const connection: RuntimeConnection = {
      baseUrl: "http://127.0.0.1:8765",
      token: "test-token",
    };
    const ticket = await acquireWsTicket("audio", connection);
    expect(ticket).toBe("audio-ticket-456");
    expect(fetchSpy).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/v1/runtime/ws-ticket?purpose=audio",
      expect.objectContaining({ method: "POST" }),
    );
    fetchSpy.mockRestore();
  });

  it("throws fail-fast error if response is not ok", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("Unauthorized", { status: 401 }));
    const connection: RuntimeConnection = {
      baseUrl: "http://127.0.0.1:8765",
      token: "test-token",
    };
    await expect(acquireWsTicket("events", connection)).rejects.toThrow(
      "获取 Runtime WebSocket Ticket 失败 (401)",
    );
    fetchSpy.mockRestore();
  });
});

describe("runtimeFetchWithConnection", () => {
  it("attaches Bearer token to requests on the same origin", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("{}", { status: 200 }));
    const connection: RuntimeConnection = {
      baseUrl: "http://127.0.0.1:8765",
      token: "secret-token-xyz",
    };
    await runtimeFetchWithConnection(connection, "/v1/health");
    const expectedHeaders = new Headers();
    expectedHeaders.set("Authorization", "Bearer secret-token-xyz");
    expect(fetchSpy).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/v1/health",
      expect.objectContaining({
        headers: expectedHeaders,
      }),
    );
    fetchSpy.mockRestore();
  });

  it("refuses to send Runtime credentials cross-origin", async () => {
    const connection: RuntimeConnection = {
      baseUrl: "http://127.0.0.1:8765",
      token: "secret-token-xyz",
    };
    await expect(
      runtimeFetchWithConnection(connection, "https://attacker.com/steal"),
    ).rejects.toThrow("Refusing to send Runtime credentials cross-origin");
  });
});

describe("runtimeWebSocketUrlFromConnection", () => {
  it("derives ws and wss urls correctly", () => {
    expect(
      runtimeWebSocketUrlFromConnection({ baseUrl: "http://127.0.0.1:8765" }),
    ).toBe("ws://127.0.0.1:8765");
    expect(
      runtimeWebSocketUrlFromConnection({
        baseUrl: "https://remote.test:8443/",
      }),
    ).toBe("wss://remote.test:8443");
  });
});
