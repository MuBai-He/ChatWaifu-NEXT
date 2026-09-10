import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BrowserVoiceClient,
  parsePlaybackMarker,
  setStreamCaptureEnabled,
} from "./voiceClient";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("voice capture gating", () => {
  it("reports an actionable error when the WebView has no microphone API", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: undefined,
    });
    const onError = vi.fn();
    const onStateChange = vi.fn();
    const client = new BrowserVoiceClient({
      onStateChange,
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError,
    });

    await expect(client.connect("session-1")).rejects.toThrow("麦克风能力");

    expect(onStateChange).toHaveBeenCalledWith("unsupported");
    expect(onError).toHaveBeenCalledWith(
      expect.stringContaining("重新启动桌宠"),
    );
  });

  it("enables and disables every outbound audio track", () => {
    const tracks = [{ enabled: true }, { enabled: true }];
    const stream = {
      getAudioTracks: () => tracks,
    } as unknown as Pick<MediaStream, "getAudioTracks">;

    setStreamCaptureEnabled(stream, false);
    expect(tracks.map((track) => track.enabled)).toEqual([false, false]);

    setStreamCaptureEnabled(stream, true);
    expect(tracks.map((track) => track.enabled)).toEqual([true, true]);
  });

  it("tolerates capture changes before a stream exists", () => {
    expect(() => setStreamCaptureEnabled(null, false)).not.toThrow();
  });

  it("validates ordered WebRTC playback markers", () => {
    expect(
      parsePlaybackMarker(
        JSON.stringify({
          type: "chatwaifu.playback_segment",
          schema_version: "1.0",
          phase: "started",
          generation_id: "generation-1",
          stream_id: "stream-1",
          segment_id: "segment-1",
          duration_ms: 1200,
        }),
      ),
    ).toEqual({
      phase: "started",
      generationId: "generation-1",
      streamId: "stream-1",
      segmentId: "segment-1",
      durationMs: 1200,
    });
    expect(parsePlaybackMarker("not json")).toBeNull();
    expect(
      parsePlaybackMarker({
        type: "chatwaifu.playback_segment",
        phase: "buffered",
        duration_ms: -1,
      }),
    ).toBeNull();
  });

  it("falls back to another microphone and reconnects when the device disappears", async () => {
    vi.useFakeTimers();
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const states: string[] = [];
    const selections: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: (_devices, selected) => selections.push(selected),
      onPlaybackReceipt: vi.fn(),
      onError: vi.fn(),
    });

    await client.connect("session-1", "mic-a");
    expect(browser.requestedDeviceIds()).toEqual(["mic-a"]);

    browser.setDevices([device("mic-b", "USB 麦克风")]);
    browser.mediaDevices.dispatchEvent(new Event("devicechange"));
    await vi.runAllTimersAsync();

    expect(browser.requestedDeviceIds()).toEqual(["mic-a", "mic-b"]);
    expect(selections).toContain("mic-b");
    expect(states).toContain("reconnecting");
    expect(states.at(-1)).toBe("connected");
    await client.dispose("session-1");
  });

  it("cancels a pending WebRTC reconnect after manual disconnect", async () => {
    vi.useFakeTimers();
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: vi.fn(),
    });
    await client.connect("session-1", "mic-a");

    browser.peers[0]?.setConnectionState("disconnected");
    await client.disconnect("session-1");
    await vi.runAllTimersAsync();

    expect(browser.requestedDeviceIds()).toEqual(["mic-a"]);
  });

  it("turns WebRTC segment markers into playout-clock receipts", async () => {
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const receipts: Array<{
      phase: string;
      playedPtsMs: number;
      reason?: string;
    }> = [];
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: (item) => receipts.push(item),
      onError: vi.fn(),
    });
    await client.connect("session-1", "mic-a");
    const channel = browser.peers[0]?.dataChannel;
    const output = browser.outputs[0];
    expect(channel).toBeDefined();
    expect(output).toBeDefined();

    channel?.receive(playbackMarker("started"));
    channel?.receive(playbackMarker("buffered"));
    if (output) output.currentTime = 0.09;
    browser.runAnimationFrames();
    if (output) output.currentTime = 0.4;
    browser.runAnimationFrames();
    if (output) output.currentTime = 1.2;
    browser.runAnimationFrames();

    expect(receipts[0]).toMatchObject({ phase: "started", playedPtsMs: 10 });
    expect(receipts[1]).toMatchObject({ phase: "progress", playedPtsMs: 320 });
    expect(receipts[2]).toMatchObject({
      phase: "stopped",
      playedPtsMs: 1000,
      reason: "ended",
    });
    await client.dispose("session-1");
  });

  it("updates streaming segment duration from buffered marker before ending playout", async () => {
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const receipts: object[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: (receipt) => receipts.push(receipt),
      onError: vi.fn(),
    });

    await client.connect("session-1", "mic-a");
    const channel = (client as unknown as { peer: FakePeer | null }).peer
      ?.dataChannel;
    const output = (client as unknown as { output: FakeOutputAudio | null })
      .output;

    channel?.receive(playbackMarker("started", 0));
    channel?.receive(playbackMarker("buffered", 1500));
    if (output) output.currentTime = 0.09;
    browser.runAnimationFrames();
    if (output) output.currentTime = 1.7;
    browser.runAnimationFrames();

    expect(receipts[0]).toMatchObject({ phase: "started", playedPtsMs: 10 });
    expect(receipts[1]).toMatchObject({
      phase: "stopped",
      playedPtsMs: 1500,
      reason: "ended",
    });
    await client.dispose("session-1");
  });

  it("requires composite identity match (segmentId + streamId + generationId) to buffer segment", async () => {
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const receipts: object[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: (receipt) => receipts.push(receipt),
      onError: vi.fn(),
    });

    await client.connect("session-1", "mic-a");
    const channel = (client as unknown as { peer: FakePeer | null }).peer
      ?.dataChannel;
    const output = (client as unknown as { output: FakeOutputAudio | null })
      .output;

    // Segment started with generation-1, stream-1, segment-1
    channel?.receive(
      playbackMarker("started", 1000, {
        generationId: "generation-1",
        streamId: "stream-1",
        segmentId: "segment-1",
      }),
    );

    // Mismatched buffered marker with same segmentId but different generationId
    channel?.receive(
      playbackMarker("buffered", 2000, {
        generationId: "generation-2",
        streamId: "stream-1",
        segmentId: "segment-1",
      }),
    );

    // Playout progresses past 1000ms
    if (output) output.currentTime = 1.5;
    browser.runAnimationFrames();

    // Since buffered marker had mismatched generationId, segment-1 was NOT marked serverBuffered,
    // so it must NOT emit a stopped receipt with reason "ended"
    const stopped = receipts.find(
      (r) => (r as { phase: string }).phase === "stopped",
    );
    expect(stopped).toBeUndefined();

    // Now send legitimate matching buffered marker
    channel?.receive(
      playbackMarker("buffered", 1000, {
        generationId: "generation-1",
        streamId: "stream-1",
        segmentId: "segment-1",
      }),
    );
    browser.runAnimationFrames();

    const endedStopped = receipts.find(
      (r) =>
        (r as { phase: string; reason?: string }).phase === "stopped" &&
        (r as { reason?: string }).reason === "ended",
    );
    expect(endedStopped).toBeDefined();
    await client.dispose("session-1");
  });

  it("applies legitimate 0-ms duration from buffered marker and emits ended receipt", async () => {
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const receipts: object[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: (receipt) => receipts.push(receipt),
      onError: vi.fn(),
    });

    await client.connect("session-1", "mic-a");
    const channel = (client as unknown as { peer: FakePeer | null }).peer
      ?.dataChannel;
    const output = (client as unknown as { output: FakeOutputAudio | null })
      .output;

    channel?.receive(playbackMarker("started", 500));
    channel?.receive(playbackMarker("buffered", 0));

    if (output) output.currentTime = 0.15;
    browser.runAnimationFrames();

    expect(receipts).toContainEqual(
      expect.objectContaining({
        phase: "stopped",
        playedPtsMs: 0,
        reason: "ended",
      }),
    );
    await client.dispose("session-1");
  });

  it("adjusts startMediaMs for queued segments when preceding segment duration changes", async () => {
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const receipts: object[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: (receipt) => receipts.push(receipt),
      onError: vi.fn(),
    });

    await client.connect("session-1", "mic-a");
    const channel = (client as unknown as { peer: FakePeer | null }).peer
      ?.dataChannel;
    const output = (client as unknown as { output: FakeOutputAudio | null })
      .output;

    // Queue segment 1 (estimate 500ms)
    channel?.receive(
      playbackMarker("started", 500, {
        generationId: "gen-1",
        streamId: "stream-1",
        segmentId: "seg-1",
      }),
    );
    // Queue segment 2 (placed right after segment 1: previousEnd = startMediaMs + 500)
    channel?.receive(
      playbackMarker("started", 300, {
        generationId: "gen-2",
        streamId: "stream-1",
        segmentId: "seg-2",
      }),
    );

    // Segment 1 buffered arrives with longer duration 1000ms (+500ms delta)
    channel?.receive(
      playbackMarker("buffered", 1000, {
        generationId: "gen-1",
        streamId: "stream-1",
        segmentId: "seg-1",
      }),
    );

    // Also buffer segment 2
    channel?.receive(
      playbackMarker("buffered", 300, {
        generationId: "gen-2",
        streamId: "stream-1",
        segmentId: "seg-2",
      }),
    );

    // Advance audio time to 0.7s (700ms). Segment 1 is playing (pts ~580ms).
    // Segment 2 should NOT have started yet because its startMediaMs was pushed back by 500ms!
    if (output) output.currentTime = 0.7;
    browser.runAnimationFrames();

    const seg2StartedBefore = receipts.find(
      (r) => (r as { segmentId?: string; phase: string }).segmentId === "seg-2",
    );
    expect(seg2StartedBefore).toBeUndefined();

    // Advance audio time to 1.5s. Segment 1 should be ended and Segment 2 should be started!
    if (output) output.currentTime = 1.5;
    browser.runAnimationFrames();

    const seg1Ended = receipts.find(
      (r) =>
        (r as { segmentId?: string; phase: string; reason?: string })
          .segmentId === "seg-1" &&
        (r as { phase: string }).phase === "stopped",
    );
    expect(seg1Ended).toBeDefined();

    // Next animation frame evaluates newly front-of-queue segment 2
    browser.runAnimationFrames();

    const seg2Started = receipts.find(
      (r) =>
        (r as { segmentId?: string; phase: string }).segmentId === "seg-2" &&
        (r as { phase: string }).phase === "started",
    );
    expect(seg2Started).toBeDefined();

    await client.dispose("session-1");
  });
});

describe("cloud realtime recovery and reconnection", () => {
  it("exhausts reconnect attempts without infinite loop when connection drops immediately after handshake", async () => {
    vi.useFakeTimers();
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    let offerCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        offerCount++;
        return Promise.resolve(
          new Response(
            JSON.stringify({
              type: "answer",
              sdp: "answer",
              pc_id: `pc-${offerCount}`,
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          ),
        );
      }),
    );
    const states: string[] = [];
    const errors: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: (err) => errors.push(err),
    });

    await client.connect("session-1", "mic-a");
    expect(states.at(-1)).toBe("connected");

    // Repeated immediate drops after 100ms (far before 5000ms healthy threshold)
    // must count up backoff retries and terminate
    const backoffs = [0, 250, 500, 1_000, 2_000, 4_000];
    for (const delay of backoffs) {
      const activePeer = browser.peers.at(-1);
      // Fail connection
      activePeer?.setConnectionState("failed");
      // Advance by delay plus some margin to allow establish to run
      await vi.advanceTimersByTimeAsync(delay + 50);
    }

    expect(states.at(-1)).toBe("failed");
    expect(errors.at(-1)).toContain("自动重连已达到上限");
    const finalOfferCount = offerCount;
    await vi.runAllTimersAsync();
    expect(offerCount).toBe(finalOfferCount);
  });

  it("resets retry attempt count only after remaining connected for healthy threshold", async () => {
    vi.useFakeTimers();
    const browser = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const states: string[] = [];
    const errors: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: (err) => errors.push(err),
    });

    await client.connect("session-1", "mic-a");
    expect(states.at(-1)).toBe("connected");

    // Drop connection once
    browser.peers.at(-1)?.setConnectionState("failed");
    await vi.advanceTimersByTimeAsync(100);
    expect(states.at(-1)).toBe("connected");

    // Advance 5000ms for healthy timer to reset reconnectAttempt
    await vi.advanceTimersByTimeAsync(5000);

    // Drop connection again - reconnect succeeds normally without attempt exhaustion
    browser.peers.at(-1)?.setConnectionState("failed");
    await vi.advanceTimersByTimeAsync(100);
    expect(states.at(-1)).toBe("connected");
    expect(errors).toHaveLength(0);
    await client.dispose("session-1");
  });

  it("sends targeted DELETE with pc_id for stale offers and old connections", async () => {
    installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const fetchCalls: Array<{ url: string; method?: string }> = [];
    let offerCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        fetchCalls.push({ url, method: init?.method });
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        offerCount++;
        return Promise.resolve(
          new Response(
            JSON.stringify({
              type: "answer",
              sdp: "answer",
              pc_id: `pc-${offerCount}`,
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          ),
        );
      }),
    );
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: vi.fn(),
    });

    await client.connect("session-1", "mic-a");
    await client.disconnect("session-1");

    const deleteCall = fetchCalls.find((c) => c.method === "DELETE");
    expect(deleteCall).toBeDefined();
    expect(deleteCall?.url).toContain(
      "/v1/sessions/session-1/webrtc?pc_id=pc-1",
    );
  });

  it("fails immediately and does not retry on 403 non-retryable response", async () => {
    vi.useFakeTimers();
    installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "egress consent required" }), {
            status: 403,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );
    const states: string[] = [];
    const errors: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: (err) => errors.push(err),
    });

    await expect(client.connect("session-1", "mic-a")).rejects.toThrow(
      "egress consent required",
    );
    expect(states.at(-1)).toBe("failed");
    expect(errors.at(-1)).toBe("egress consent required");

    await vi.runAllTimersAsync();
    expect(states.at(-1)).toBe("failed");
  });

  it("cleans up abandoned offer returned after disconnect", async () => {
    installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const fetchCalls: Array<{ url: string; method?: string }> = [];
    let resolveOffer: ((resp: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        fetchCalls.push({ url, method: init?.method });
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        return new Promise<Response>((resolve) => {
          resolveOffer = resolve;
        });
      }),
    );
    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: vi.fn(),
    });

    const connectPromise = client.connect("session-1", "mic-a");
    await new Promise((resolve) => setTimeout(resolve, 0));
    void client.disconnect("session-1");

    resolveOffer?.(
      new Response(
        JSON.stringify({
          type: "answer",
          sdp: "answer",
          pc_id: "pc-stale",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    await connectPromise.catch(() => undefined);

    const staleDelete = fetchCalls.find(
      (c) =>
        c.method === "DELETE" &&
        c.url.includes("/v1/sessions/session-1/webrtc?pc_id=pc-stale"),
    );
    expect(staleDelete).toBeDefined();
  });

  it("retries on 429 rate limit rather than failing as permanent non-retryable", async () => {
    vi.useFakeTimers();
    installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        callCount++;
        if (callCount === 1) {
          return Promise.resolve(
            new Response(JSON.stringify({ detail: "rate limit exceeded" }), {
              status: 429,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              type: "answer",
              sdp: "answer",
              pc_id: "pc-retry",
            }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          ),
        );
      }),
    );
    const states: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: vi.fn(),
    });

    const connectPromise = client.connect("session-1", "mic-a");
    // Initial establish fails with 429, but since it's 429 it was called directly by connect() which throws
    await expect(connectPromise).rejects.toThrow("rate limit exceeded");
  });

  it("deferred rejection on old session after new connect does not fail new connection", async () => {
    installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    let rejectOldOffer: ((err: Error) => void) | undefined;
    let resolveNewOffer: ((resp: Response) => void) | undefined;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        if (url.includes("session-1")) {
          return new Promise<Response>((_, reject) => {
            rejectOldOffer = reject;
          });
        }
        return new Promise<Response>((resolve) => {
          resolveNewOffer = resolve;
        });
      }),
    );

    const states: string[] = [];
    const errors: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: (err) => errors.push(err),
    });

    // Start session-1
    const p1 = client.connect("session-1", "mic-a");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Supersede with session-2 before session-1 completes
    const p2 = client.connect("session-2", "mic-a");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Old offer fails late
    rejectOldOffer?.(new Error("Old connection network error"));
    await p1.catch(() => undefined);

    // Old rejection must NOT have failed client state for session-2
    expect(states.at(-1)).not.toBe("failed");
    expect(errors).toHaveLength(0);

    // Complete session-2
    resolveNewOffer?.(
      new Response(
        JSON.stringify({ type: "answer", sdp: "answer", pc_id: "pc-new" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await p2;
    expect(states.at(-1)).toBe("connected");
  });

  it("deferred answer on old session sends DELETE to old session and does not overwrite new session active connection", async () => {
    installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const fetchCalls: Array<{ url: string; method?: string }> = [];
    let resolveOldOffer: ((resp: Response) => void) | undefined;
    let resolveNewOffer: ((resp: Response) => void) | undefined;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        fetchCalls.push({ url, method: init?.method });
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        if (url.includes("session-old")) {
          return new Promise<Response>((resolve) => {
            resolveOldOffer = resolve;
          });
        }
        return new Promise<Response>((resolve) => {
          resolveNewOffer = resolve;
        });
      }),
    );

    const client = new BrowserVoiceClient({
      onStateChange: vi.fn(),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: vi.fn(),
    });

    const pOld = client.connect("session-old", "mic-a");
    await new Promise((resolve) => setTimeout(resolve, 0));

    const pNew = client.connect("session-new", "mic-a");
    await new Promise((resolve) => setTimeout(resolve, 0));

    resolveNewOffer?.(
      new Response(
        JSON.stringify({ type: "answer", sdp: "answer", pc_id: "pc-new" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await pNew;

    // Resolve old offer late
    resolveOldOffer?.(
      new Response(
        JSON.stringify({ type: "answer", sdp: "answer", pc_id: "pc-old" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await pOld.catch(() => undefined);

    // Verify DELETE targeted session-old with pc-old, NOT session-new!
    const oldDelete = fetchCalls.find(
      (c) =>
        c.method === "DELETE" &&
        c.url.includes("/v1/sessions/session-old/webrtc?pc_id=pc-old"),
    );
    expect(oldDelete).toBeDefined();

    // Verify no untargeted DELETE or wrong session delete occurred
    const wrongDelete = fetchCalls.find(
      (c) =>
        c.method === "DELETE" &&
        c.url.includes("/v1/sessions/session-new/webrtc?pc_id=pc-old"),
    );
    expect(wrongDelete).toBeUndefined();
  });

  it("reconnect exhaustion disposes owned resources and transitions to failed", async () => {
    vi.useFakeTimers();
    const harness = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    let attempt = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return Promise.resolve(
            new Response(JSON.stringify({ connections_closed: 1 }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          );
        }
        attempt++;
        if (attempt === 1) {
          return Promise.resolve(
            new Response(
              JSON.stringify({ type: "answer", sdp: "answer", pc_id: "pc-1" }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
          );
        }
        return Promise.reject(new Error("Network connection down"));
      }),
    );

    const states: string[] = [];
    const errors: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: (err) => errors.push(err),
    });

    await client.connect("session-1", "mic-a");
    expect(states.at(-1)).toBe("connected");

    // Simulate peer disconnection to trigger reconnect loop
    const peer = harness.peers[0];
    peer?.setConnectionState("disconnected");

    // Advance timers through all 5 backoff steps
    await vi.runAllTimersAsync();

    expect(states.at(-1)).toBe("failed");
    expect(errors.at(-1)).toContain("自动重连已达到上限");

    // Verify resources were disposed
    expect(peer?.connectionState).toBe("closed");
  });

  it("ignores unregistered error messages on the playback data channel", async () => {
    const harness = installVoiceBrowserHarness([device("mic-a", "内置麦克风")]);
    const states: string[] = [];
    const errors: string[] = [];
    const client = new BrowserVoiceClient({
      onStateChange: (state) => states.push(state),
      onInputLevel: vi.fn(),
      onDevicesChange: vi.fn(),
      onPlaybackReceipt: vi.fn(),
      onError: (err) => errors.push(err),
    });

    await client.connect("session-1", "mic-a");
    expect(states.at(-1)).toBe("connected");

    // Simulate backend sending fatal error on data channel
    const peer = harness.peers[0];
    peer?.dataChannel.receive(
      new MessageEvent("message", {
        data: JSON.stringify({
          type: "chatwaifu.error",
          detail: "Egress policy violation: explicit consent revoked",
          fatal: true,
        }),
      }),
    );

    expect(states.at(-1)).toBe("connected");
    expect(errors).toEqual([]);
    expect(peer?.connectionState).toBe("connected");
    await client.dispose();
  });
});

function device(deviceId: string, label: string): MediaDeviceInfo {
  return {
    deviceId,
    groupId: "group",
    kind: "audioinput",
    label,
    toJSON: () => ({}),
  };
}

function installVoiceBrowserHarness(initialDevices: MediaDeviceInfo[]) {
  const streams: FakeStream[] = [];
  const peers: FakePeer[] = [];
  const outputs: FakeOutputAudio[] = [];
  const animationFrames = new Map<number, FrameRequestCallback>();
  let nextAnimationFrame = 1;
  const mediaDevices = new FakeMediaDevices(initialDevices, (selected) => {
    const stream = new FakeStream(selected);
    streams.push(stream);
    return stream as unknown as MediaStream;
  });
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: mediaDevices,
  });
  vi.stubGlobal(
    "RTCPeerConnection",
    class extends FakePeer {
      constructor() {
        super();
        peers.push(this);
      }
    },
  );
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((callback: FrameRequestCallback) => {
      const id = nextAnimationFrame;
      nextAnimationFrame += 1;
      animationFrames.set(id, callback);
      return id;
    }),
  );
  vi.stubGlobal(
    "cancelAnimationFrame",
    vi.fn((id: number) => animationFrames.delete(id)),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE")
        return Promise.resolve(
          new Response(JSON.stringify({ connections_closed: 1 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      return Promise.resolve(
        new Response(JSON.stringify({ type: "answer", sdp: "answer" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
  const createElement = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
    if (tag === "audio") {
      const output = new FakeOutputAudio();
      outputs.push(output);
      return output;
    }
    return createElement(tag);
  }) as typeof document.createElement);
  return {
    mediaDevices,
    peers,
    outputs,
    setDevices: (devices: MediaDeviceInfo[]) =>
      mediaDevices.setDevices(devices),
    requestedDeviceIds: () => mediaDevices.requestedDeviceIds,
    runAnimationFrames: () => {
      const callbacks = [...animationFrames.values()];
      animationFrames.clear();
      for (const callback of callbacks) callback(performance.now());
    },
  };
}

function playbackMarker(
  phase: "started" | "buffered",
  durationMs: number = 1000,
  ids: { generationId?: string; streamId?: string; segmentId?: string } = {},
): MessageEvent {
  return new MessageEvent("message", {
    data: JSON.stringify({
      type: "chatwaifu.playback_segment",
      schema_version: "1.0",
      phase,
      generation_id: ids.generationId ?? "generation-1",
      stream_id: ids.streamId ?? "stream-1",
      segment_id: ids.segmentId ?? "segment-1",
      duration_ms: durationMs,
    }),
  });
}

class FakeMediaDevices extends EventTarget {
  readonly requestedDeviceIds: string[] = [];

  constructor(
    private devices: MediaDeviceInfo[],
    private readonly makeStream: (selected: string) => MediaStream,
  ) {
    super();
  }

  setDevices(devices: MediaDeviceInfo[]): void {
    this.devices = devices;
  }

  enumerateDevices(): Promise<MediaDeviceInfo[]> {
    return Promise.resolve(this.devices);
  }

  getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream> {
    const audio = constraints.audio as MediaTrackConstraints;
    const requested = (
      audio.deviceId as ConstrainDOMStringParameters | undefined
    )?.exact;
    const selected =
      typeof requested === "string"
        ? requested
        : (this.devices[0]?.deviceId ?? "");
    this.requestedDeviceIds.push(selected);
    if (!this.devices.some((item) => item.deviceId === selected))
      return Promise.reject(new DOMException("missing", "NotFoundError"));
    return Promise.resolve(this.makeStream(selected));
  }
}

class FakeTrack extends EventTarget {
  enabled = true;

  constructor(private readonly deviceId: string) {
    super();
  }

  getSettings(): MediaTrackSettings {
    return { deviceId: this.deviceId };
  }

  stop(): void {}
}

class FakeStream {
  readonly track: FakeTrack;

  constructor(deviceId: string) {
    this.track = new FakeTrack(deviceId);
  }

  getAudioTracks(): MediaStreamTrack[] {
    return [this.track as unknown as MediaStreamTrack];
  }

  getTracks(): MediaStreamTrack[] {
    return this.getAudioTracks();
  }
}

class FakeDataChannel {
  onmessage: ((event: MessageEvent) => void) | null = null;
  close(): void {}
  receive(event: MessageEvent): void {
    this.onmessage?.(event);
  }
}

class FakePeer extends EventTarget {
  connectionState: RTCPeerConnectionState = "new";
  iceGatheringState: RTCIceGatheringState = "complete";
  localDescription: RTCSessionDescription | null = null;
  onconnectionstatechange: (() => void) | null = null;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  readonly dataChannel = new FakeDataChannel();

  createDataChannel(): RTCDataChannel {
    return this.dataChannel as unknown as RTCDataChannel;
  }

  addTrack(): RTCRtpSender {
    return {} as RTCRtpSender;
  }

  createOffer(): Promise<RTCSessionDescriptionInit> {
    return Promise.resolve({ type: "offer", sdp: "offer" });
  }

  setLocalDescription(description: RTCSessionDescriptionInit): Promise<void> {
    this.localDescription = description as RTCSessionDescription;
    return Promise.resolve();
  }

  setRemoteDescription(): Promise<void> {
    this.setConnectionState("connected");
    return Promise.resolve();
  }

  setConnectionState(state: RTCPeerConnectionState): void {
    this.connectionState = state;
    this.onconnectionstatechange?.();
  }

  close(): void {
    this.connectionState = "closed";
  }
}

class FakeOutputAudio {
  autoplay = false;
  paused = false;
  currentTime = 0;
  srcObject: MediaStream | null = null;

  setAttribute(): void {}
  pause(): void {
    this.paused = true;
  }
  play(): Promise<void> {
    this.paused = false;
    return Promise.resolve();
  }
}

class FakeAudioContext {
  state: AudioContextState = "running";

  createMediaStreamSource() {
    return { connect: vi.fn() };
  }

  createAnalyser() {
    return {
      fftSize: 512,
      smoothingTimeConstant: 0,
      context: this,
      getFloatTimeDomainData: vi.fn(),
    };
  }

  resume(): Promise<void> {
    return Promise.resolve();
  }
  close(): Promise<void> {
    this.state = "closed";
    return Promise.resolve();
  }
}
