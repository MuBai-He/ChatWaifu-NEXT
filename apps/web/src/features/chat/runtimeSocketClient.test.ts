import type { DomainEvent } from "@chatwaifu/protocol";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getSessionEvents } from "./runtime-client/sessionsClient";
import { runtimeWebSocketUrl } from "./runtimeEndpoint";
import { RuntimeSocketClient } from "./runtimeSocketClient";

vi.mock("./runtimeEndpoint", () => ({
  runtimeWebSocketUrl: vi.fn().mockResolvedValue("ws://runtime.test"),
}));
vi.mock("./runtime-client/sessionsClient", () => ({
  getSessionEvents: vi.fn(),
}));

const SESSION_ID = "00000000-0000-4000-8000-000000000101";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static failuresRemaining = 0;

  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(readonly url: string) {
    if (FakeWebSocket.failuresRemaining > 0) {
      FakeWebSocket.failuresRemaining -= 1;
      throw new Error("socket construction failed");
    }
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.onclose?.();
  }

  emit(event: DomainEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

describe("RuntimeSocketClient replay", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    FakeWebSocket.failuresRemaining = 0;
    vi.mocked(getSessionEvents).mockReset();
    vi.mocked(runtimeWebSocketUrl).mockReset();
    vi.mocked(runtimeWebSocketUrl).mockResolvedValue("ws://runtime.test");
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("fills a sequence gap over HTTP, de-duplicates, and reconnects from the cursor", async () => {
    const received: number[] = [];
    vi.mocked(getSessionEvents).mockResolvedValue([event(3)]);
    const client = new RuntimeSocketClient(
      {
        onConnection: vi.fn(),
        onEvent: (value) => {
          if (typeof value.sequence === "number") received.push(value.sequence);
        },
        onAudio: vi.fn(),
        onProtocolError: vi.fn(),
      },
      false,
      0,
    );

    client.start(SESSION_ID, 2);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const first = FakeWebSocket.instances[0];
    expect(first.url).toContain(`session_id=${SESSION_ID}`);
    expect(first.url).toContain("after_sequence=2");

    first.emit(event(4));
    await vi.waitFor(() => expect(received).toEqual([3, 4]));
    expect(getSessionEvents).toHaveBeenCalledWith(SESSION_ID, 2);
    first.emit(event(4));
    await Promise.resolve();
    expect(received).toEqual([3, 4]);

    first.onclose?.();
    await vi.runAllTimersAsync();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    expect(FakeWebSocket.instances[1].url).toContain("after_sequence=4");
    client.stop();
  });

  it("ignores endpoint resolutions and socket callbacks from an older connection epoch", async () => {
    const firstEndpoint = deferred<string>();
    vi.mocked(runtimeWebSocketUrl)
      .mockImplementationOnce(() => firstEndpoint.promise)
      .mockResolvedValue("ws://runtime.new");
    const received: number[] = [];
    const onConnection = vi.fn();
    const client = new RuntimeSocketClient(
      {
        onConnection,
        onEvent: (value) => {
          if (typeof value.sequence === "number") received.push(value.sequence);
        },
        onAudio: vi.fn(),
        onProtocolError: vi.fn(),
      },
      false,
      0,
    );

    client.start(SESSION_ID, 1);
    client.restartFrom(5);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const current = FakeWebSocket.instances[0];
    expect(current.url).toContain("after_sequence=5");
    firstEndpoint.resolve("ws://runtime.old");
    await flushPromises();

    expect(FakeWebSocket.instances).toHaveLength(1);
    current.emit(event(6));
    await vi.waitFor(() => expect(received).toEqual([6]));
    client.restartFrom(6);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    current.emit(event(7));
    current.onopen?.();
    await flushPromises();
    expect(received).toEqual([6]);
    client.stop();
  });

  it("retries when initial endpoint or WebSocket construction fails", async () => {
    FakeWebSocket.failuresRemaining = 1;
    const onProtocolError = vi.fn();
    const client = new RuntimeSocketClient(
      {
        onConnection: vi.fn(),
        onEvent: vi.fn(),
        onAudio: vi.fn(),
        onProtocolError,
      },
      false,
      25,
    );

    client.start(SESSION_ID);
    await vi.waitFor(() => expect(onProtocolError).toHaveBeenCalledOnce());
    expect(FakeWebSocket.instances).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(25);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(runtimeWebSocketUrl).toHaveBeenLastCalledWith(true);
    client.stop();
  });

  it("rejects a replay that is still discontinuous and reconnects from the last safe cursor", async () => {
    vi.mocked(getSessionEvents).mockResolvedValue([event(4)]);
    const received: number[] = [];
    const onProtocolError = vi.fn();
    const client = new RuntimeSocketClient(
      {
        onConnection: vi.fn(),
        onEvent: (value) => {
          if (typeof value.sequence === "number") received.push(value.sequence);
        },
        onAudio: vi.fn(),
        onProtocolError,
      },
      false,
      10,
    );

    client.start(SESSION_ID, 2);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    FakeWebSocket.instances[0].emit(event(5));
    await vi.waitFor(() => expect(onProtocolError).toHaveBeenCalledOnce());
    expect(received).toEqual([]);
    await vi.advanceTimersByTimeAsync(10);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    expect(FakeWebSocket.instances[1].url).toContain("after_sequence=2");
    client.stop();
  });
});

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function event(sequence: number): DomainEvent {
  return {
    event_id: `00000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`,
    schema_version: "1.0",
    event_type: "session.created",
    session_id: SESSION_ID,
    turn_id: null,
    generation_id: null,
    skill_run_id: null,
    sequence,
    occurred_at: "2026-08-29T00:00:00Z",
    source: "runtime.test",
    correlation_id: null,
    causation_id: null,
    privacy: "local",
    payload: { character_id: "default" },
  };
}
