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

function playbackMarker(phase: "started" | "buffered"): MessageEvent {
  return new MessageEvent("message", {
    data: JSON.stringify({
      type: "chatwaifu.playback_segment",
      schema_version: "1.0",
      phase,
      generation_id: "generation-1",
      stream_id: "stream-1",
      segment_id: "segment-1",
      duration_ms: 1000,
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
