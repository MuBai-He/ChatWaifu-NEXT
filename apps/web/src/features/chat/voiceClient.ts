import type { PlaybackAckReceipt } from "./runtimeClient";
import {
  resolveRuntimeConnection,
  runtimeFetchWithConnection,
  type RuntimeConnection,
} from "./runtimeEndpoint";

export type VoiceConnectionState =
  | "unsupported"
  | "disconnected"
  | "requesting"
  | "connecting"
  | "reconnecting"
  | "connected"
  | "failed";

export type VoiceActivationMode = "push_to_talk" | "open_mic";

export interface VoiceDevice {
  deviceId: string;
  label: string;
}

export interface VoiceClientCallbacks {
  onStateChange: (state: VoiceConnectionState) => void;
  onInputLevel: (level: number) => void;
  onDevicesChange: (devices: VoiceDevice[], selectedDeviceId: string) => void;
  onPlaybackReceipt: (receipt: PlaybackAckReceipt) => void;
  onError: (message: string) => void;
}

interface RemotePlaybackSegment {
  generationId: string;
  streamId: string;
  segmentId: string;
  durationMs: number;
  startMediaMs: number;
  serverBuffered: boolean;
  started: boolean;
  lastReportedMs: number;
}

export interface PlaybackMarker {
  phase: "started" | "buffered";
  generationId: string;
  streamId: string;
  segmentId: string;
  durationMs: number;
}

const RECONNECT_BACKOFF_MS = [250, 500, 1_000, 2_000, 4_000] as const;
const DISCONNECTED_GRACE_MS = 750;
const REMOTE_PLAYOUT_LEAD_MS = 80;

export class BrowserVoiceClient {
  private static readonly HEALTHY_THRESHOLD_MS = 5_000;
  private peer: RTCPeerConnection | null = null;
  private stream: MediaStream | null = null;
  private output: HTMLAudioElement | null = null;
  private dataChannel: RTCDataChannel | null = null;
  private audioContext: AudioContext | null = null;
  private meterFrame: number | null = null;
  private playbackFrame: number | null = null;
  private reconnectTimer: number | null = null;
  private healthyTimer: number | null = null;
  private captureEnabled = false;
  private activationMode: VoiceActivationMode = "push_to_talk";
  private disposed = false;
  private desiredConnected = false;
  private sessionId: string | null = null;
  private requestedDeviceId = "";
  private activeDeviceId = "";
  private activePcId: string | null = null;
  private activeRuntime: RuntimeConnection | null = null;
  private activeSessionId: string | null = null;
  private connectionEpoch = 0;
  private reconnectAttempt = 0;
  private deviceListenerInstalled = false;
  private readonly remotePlayback: RemotePlaybackSegment[] = [];

  constructor(private readonly callbacks: VoiceClientCallbacks) {}

  static supported(): boolean {
    return BrowserVoiceClient.unavailableReason() === null;
  }

  static unavailableReason(): string | null {
    if (
      typeof navigator === "undefined" ||
      typeof navigator.mediaDevices?.getUserMedia !== "function"
    ) {
      return "当前桌宠没有获得麦克风能力。请完全退出并重新启动桌宠，然后允许系统麦克风权限。";
    }
    if (typeof RTCPeerConnection !== "function") {
      return "当前系统 WebView 不支持实时语音连接，请更新系统后重新启动桌宠。";
    }
    return null;
  }

  async listInputDevices(): Promise<VoiceDevice[]> {
    if (!BrowserVoiceClient.supported()) return [];
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter((device) => device.kind === "audioinput")
      .map((device, index) => ({
        deviceId: device.deviceId,
        label: device.label || `麦克风 ${index + 1}`,
      }));
  }

  async connect(sessionId: string, deviceId?: string): Promise<void> {
    if (this.disposed) return;
    const unavailableReason = BrowserVoiceClient.unavailableReason();
    if (unavailableReason) {
      this.callbacks.onStateChange("unsupported");
      this.callbacks.onError(unavailableReason);
      throw new Error(unavailableReason);
    }
    this.desiredConnected = true;
    this.sessionId = sessionId;
    this.requestedDeviceId = deviceId ?? "";
    this.reconnectAttempt = 0;
    this.cancelReconnect();
    this.cancelHealthyTimer();
    this.installDeviceListener();
    const currentEpoch = this.connectionEpoch + 1;
    try {
      await this.establish(false);
    } catch (error: unknown) {
      if (this.connectionEpoch !== currentEpoch || !this.desiredConnected) {
        return;
      }
      this.desiredConnected = false;
      await this.teardownConnection(true);
      if (this.connectionEpoch !== currentEpoch || this.disposed) return;
      this.removeDeviceListener();
      this.callbacks.onStateChange("failed");
      this.callbacks.onError(voiceErrorMessage(error));
      throw error;
    }
  }

  async disconnect(sessionId?: string): Promise<void> {
    this.desiredConnected = false;
    this.connectionEpoch += 1;
    this.cancelReconnect();
    this.cancelHealthyTimer();
    this.removeDeviceListener();
    this.stopRemotePlayback(undefined, "interrupted");
    if (sessionId) this.sessionId = sessionId;
    await this.teardownConnection(true);
    if (!this.disposed && !this.desiredConnected)
      this.callbacks.onStateChange("disconnected");
  }

  async dispose(sessionId?: string): Promise<void> {
    this.disposed = true;
    this.desiredConnected = false;
    this.connectionEpoch += 1;
    this.cancelReconnect();
    this.cancelHealthyTimer();
    this.removeDeviceListener();
    this.stopRemotePlayback(undefined, "interrupted");
    if (sessionId) this.sessionId = sessionId;
    await this.teardownConnection(true);
  }

  setCaptureEnabled(enabled: boolean): void {
    this.captureEnabled = enabled;
    this.applyCaptureState();
  }

  setActivationMode(mode: VoiceActivationMode): void {
    this.activationMode = mode;
  }

  stopRemotePlayback(
    generationId?: string,
    reason: "interrupted" | "error" = "interrupted",
  ): void {
    const output = this.output;
    const mediaNow = Math.max(0, Math.round((output?.currentTime ?? 0) * 1000));
    const retained: RemotePlaybackSegment[] = [];
    for (const segment of this.remotePlayback) {
      if (generationId && segment.generationId !== generationId) {
        retained.push(segment);
        continue;
      }
      const playedPtsMs = segment.started
        ? Math.max(
            0,
            Math.min(segment.durationMs, mediaNow - segment.startMediaMs),
          )
        : 0;
      this.callbacks.onPlaybackReceipt({
        phase: segment.started ? "stopped" : "queue_cleared",
        generationId: segment.generationId,
        streamId: segment.streamId,
        segmentId: segment.segmentId,
        playedPtsMs,
        bufferedMs: 0,
        clientClockMs: clientClockMs(),
        transport: "webrtc",
        reason: segment.started ? reason : "queue_cleared",
      });
    }
    this.remotePlayback.length = 0;
    this.remotePlayback.push(...retained);
    if (this.remotePlayback.length === 0) this.cancelPlaybackFrame();
  }

  private async establish(reconnecting: boolean): Promise<void> {
    const epoch = ++this.connectionEpoch;
    const targetSessionId = this.sessionId;
    if (!targetSessionId) {
      throw new Error("浏览器语音未指定会话 ID。");
    }
    await this.teardownConnection(true);
    if (!this.isDesired(epoch)) return;
    this.callbacks.onStateChange(reconnecting ? "reconnecting" : "requesting");

    const runtime = await resolveRuntimeConnection();
    if (!this.isDesired(epoch)) return;
    const selected = await this.resolveDeviceForConnection(reconnecting);
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: selected ? { exact: selected } : undefined,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    if (!this.isDesired(epoch)) {
      stopStream(stream);
      return;
    }

    this.stream = stream;
    this.activeRuntime = runtime;
    this.activeSessionId = targetSessionId;
    const inputTrack = stream.getAudioTracks()[0];
    this.activeDeviceId =
      inputTrack?.getSettings().deviceId ?? selected ?? this.requestedDeviceId;
    inputTrack?.addEventListener("ended", () => {
      if (this.isDesired(epoch)) {
        this.cancelHealthyTimer();
        this.triggerReconnect("麦克风设备已断开", 0);
      }
    });
    this.applyCaptureState();
    this.startInputMeter(stream);

    const output = document.createElement("audio");
    output.autoplay = true;
    output.setAttribute("playsinline", "true");
    this.output = output;

    const peer = new RTCPeerConnection({ iceServers: [] });
    this.peer = peer;
    const dataChannel = peer.createDataChannel("chatwaifu-runtime", {
      ordered: true,
    });
    this.dataChannel = dataChannel;
    dataChannel.onmessage = (event) => {
      if (this.isDesired(epoch) && peer === this.peer)
        this.handleTransportMessage(event.data);
    };
    for (const track of stream.getAudioTracks()) peer.addTrack(track, stream);
    peer.ontrack = (event) => {
      const remoteStream = event.streams[0] ?? new MediaStream([event.track]);
      output.srcObject = remoteStream;
      void output.play().catch((error: unknown) => {
        if (!isAbortError(error)) {
          this.callbacks.onError(
            "远端语音已连接，但浏览器未开始播放；请点一下页面后重连麦克风。",
          );
        }
      });
    };
    peer.onconnectionstatechange = () => {
      if (!this.isDesired(epoch) || peer !== this.peer) return;
      if (peer.connectionState === "connected") {
        this.cancelReconnect();
        this.callbacks.onStateChange("connected");
        this.cancelHealthyTimer();
        this.healthyTimer = window.setTimeout(() => {
          this.healthyTimer = null;
          if (
            this.isDesired(epoch) &&
            this.peer === peer &&
            peer.connectionState === "connected"
          ) {
            this.reconnectAttempt = 0;
          }
        }, BrowserVoiceClient.HEALTHY_THRESHOLD_MS);
      } else if (peer.connectionState === "failed") {
        this.cancelHealthyTimer();
        this.triggerReconnect("WebRTC 连接失败", 0);
      } else if (peer.connectionState === "disconnected") {
        this.cancelHealthyTimer();
        this.triggerReconnect("WebRTC 连接中断", DISCONNECTED_GRACE_MS);
      }
    };

    this.callbacks.onStateChange(reconnecting ? "reconnecting" : "connecting");
    await peer.setLocalDescription(await peer.createOffer());
    await waitForIceGathering(peer, 5_000);
    if (!this.isDesired(epoch)) {
      peer.close();
      stopStream(stream);
      return;
    }
    const local = peer.localDescription;
    if (!local) throw new Error("浏览器没有生成 WebRTC offer。");
    const response = await runtimeFetchWithConnection(
      runtime,
      `/v1/sessions/${targetSessionId}/webrtc/offer`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp: local.sdp,
          type: "offer",
          activation_mode: this.activationMode,
        }),
      },
    );
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as {
        detail?: string;
      } | null;
      const error = new Error(
        body?.detail ?? `WebRTC 连接失败 (${response.status})`,
      );
      const isTransient = response.status === 429 || response.status === 408;
      const isPermanent4xx =
        response.status >= 400 && response.status < 500 && !isTransient;
      if (isPermanent4xx) {
        (error as { nonRetryable?: boolean }).nonRetryable = true;
      }
      throw error;
    }
    const answer = (await response.json()) as RTCSessionDescriptionInit & {
      pc_id?: string;
    };
    if (!this.isDesired(epoch)) {
      if (answer.pc_id && targetSessionId) {
        void runtimeFetchWithConnection(
          runtime,
          `/v1/sessions/${targetSessionId}/webrtc?pc_id=${encodeURIComponent(answer.pc_id)}`,
          { method: "DELETE" },
        ).catch(() => undefined);
      }
      peer.close();
      stopStream(stream);
      return;
    }
    this.activePcId = answer.pc_id ?? null;
    await peer.setRemoteDescription(answer);
    const devices = await this.listInputDevices().catch(() => []);
    if (this.isDesired(epoch))
      this.callbacks.onDevicesChange(devices, this.activeDeviceId);
  }

  private async resolveDeviceForConnection(
    reconnecting: boolean,
  ): Promise<string> {
    if (!reconnecting || !this.requestedDeviceId) return this.requestedDeviceId;
    const devices = await this.listInputDevices();
    if (devices.some((device) => device.deviceId === this.requestedDeviceId))
      return this.requestedDeviceId;
    const fallback = devices[0]?.deviceId ?? "";
    this.requestedDeviceId = fallback;
    this.callbacks.onDevicesChange(devices, fallback);
    return fallback;
  }

  private triggerReconnect(detail: string, initialGraceMs = 0): void {
    if (
      !this.desiredConnected ||
      this.disposed ||
      this.reconnectTimer !== null
    ) {
      return;
    }
    if (this.reconnectAttempt >= RECONNECT_BACKOFF_MS.length) {
      this.desiredConnected = false;
      this.cancelHealthyTimer();
      this.cancelReconnect();
      this.removeDeviceListener();
      void this.teardownConnection(true);
      this.callbacks.onStateChange("failed");
      this.callbacks.onError(`${detail}，自动重连已达到上限，请手动重试。`);
      return;
    }
    const backoff =
      this.reconnectAttempt === 0
        ? initialGraceMs
        : RECONNECT_BACKOFF_MS[this.reconnectAttempt - 1];
    this.reconnectAttempt += 1;
    this.scheduleReconnect(backoff ?? 0, detail);
  }

  private scheduleReconnect(delayMs: number, detail: string): void {
    if (!this.desiredConnected || this.disposed || this.reconnectTimer !== null)
      return;
    this.callbacks.onStateChange("reconnecting");
    this.stopRemotePlayback(undefined, "interrupted");
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      const attemptEpoch = this.connectionEpoch + 1;
      void this.establish(true).catch(async (error: unknown) => {
        if (
          this.connectionEpoch !== attemptEpoch ||
          !this.desiredConnected ||
          this.disposed
        ) {
          return;
        }
        await this.teardownConnection(true);
        if (
          this.connectionEpoch !== attemptEpoch ||
          !this.desiredConnected ||
          this.disposed
        ) {
          return;
        }
        if (isPermissionError(error) || isNonRetryableError(error)) {
          this.desiredConnected = false;
          this.callbacks.onStateChange("failed");
          this.callbacks.onError(voiceErrorMessage(error));
          return;
        }
        this.triggerReconnect(detail, 0);
      });
    }, delayMs);
  }

  private cancelReconnect(): void {
    if (this.reconnectTimer === null) return;
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private cancelHealthyTimer(): void {
    if (this.healthyTimer !== null) {
      window.clearTimeout(this.healthyTimer);
      this.healthyTimer = null;
    }
  }

  private installDeviceListener(): void {
    if (this.deviceListenerInstalled) return;
    navigator.mediaDevices.addEventListener?.(
      "devicechange",
      this.handleDeviceChange,
    );
    this.deviceListenerInstalled = true;
  }

  private removeDeviceListener(): void {
    if (!this.deviceListenerInstalled || typeof navigator === "undefined")
      return;
    navigator.mediaDevices.removeEventListener?.(
      "devicechange",
      this.handleDeviceChange,
    );
    this.deviceListenerInstalled = false;
  }

  private readonly handleDeviceChange = (): void => {
    if (!this.desiredConnected || this.disposed) return;
    void this.listInputDevices()
      .then((devices) => {
        if (!this.desiredConnected || this.disposed) return;
        const selectedExists = devices.some(
          (device) => device.deviceId === this.activeDeviceId,
        );
        if (selectedExists) {
          this.callbacks.onDevicesChange(devices, this.activeDeviceId);
          return;
        }
        const fallback = devices[0]?.deviceId ?? "";
        this.requestedDeviceId = fallback;
        this.callbacks.onDevicesChange(devices, fallback);
        this.cancelHealthyTimer();
        this.triggerReconnect("麦克风设备已断开", 0);
      })
      .catch(() => {
        this.cancelHealthyTimer();
        this.triggerReconnect("无法刷新麦克风设备", 0);
      });
  };

  private handleTransportMessage(data: unknown): void {
    const marker = parsePlaybackMarker(data);
    if (!marker) return;
    if (marker.phase === "started") {
      if (
        this.remotePlayback.some(
          (segment) =>
            segment.segmentId === marker.segmentId &&
            segment.streamId === marker.streamId &&
            segment.generationId === marker.generationId,
        )
      )
        return;
      const mediaNow = Math.max(
        0,
        Math.round((this.output?.currentTime ?? 0) * 1000),
      );
      const previous = this.remotePlayback.at(-1);
      const previousEnd = previous
        ? previous.startMediaMs + previous.durationMs
        : 0;
      this.remotePlayback.push({
        ...marker,
        startMediaMs: Math.max(mediaNow + REMOTE_PLAYOUT_LEAD_MS, previousEnd),
        serverBuffered: false,
        started: false,
        lastReportedMs: 0,
      });
      this.startPlaybackFrame();
      return;
    }
    const segmentIndex = this.remotePlayback.findIndex(
      (candidate) =>
        candidate.segmentId === marker.segmentId &&
        candidate.streamId === marker.streamId &&
        candidate.generationId === marker.generationId,
    );
    if (segmentIndex !== -1) {
      const segment = this.remotePlayback[segmentIndex];
      segment.serverBuffered = true;
      if (marker.durationMs >= 0) {
        const delta = marker.durationMs - segment.durationMs;
        segment.durationMs = marker.durationMs;
        if (delta !== 0) {
          for (let i = segmentIndex + 1; i < this.remotePlayback.length; i++) {
            this.remotePlayback[i].startMediaMs += delta;
          }
        }
      }
    }
  }

  private startPlaybackFrame(): void {
    if (this.playbackFrame !== null) return;
    const update = () => {
      this.playbackFrame = null;
      if (this.disposed || this.remotePlayback.length === 0) return;
      const output = this.output;
      const segment = this.remotePlayback[0];
      if (!output || !segment) return;
      const mediaNow = Math.max(0, Math.round(output.currentTime * 1000));
      if (!output.paused && mediaNow >= segment.startMediaMs) {
        const playedPtsMs = Math.max(
          0,
          Math.min(segment.durationMs, mediaNow - segment.startMediaMs),
        );
        if (!segment.started) {
          segment.started = true;
          this.callbacks.onPlaybackReceipt(
            remoteReceipt(segment, "started", playedPtsMs),
          );
        }
        if (
          playedPtsMs < segment.durationMs &&
          playedPtsMs - segment.lastReportedMs >= 250
        ) {
          segment.lastReportedMs = playedPtsMs;
          this.callbacks.onPlaybackReceipt(
            remoteReceipt(segment, "progress", playedPtsMs),
          );
        }
        if (segment.serverBuffered && playedPtsMs >= segment.durationMs) {
          this.callbacks.onPlaybackReceipt({
            ...remoteReceipt(segment, "stopped", segment.durationMs),
            reason: "ended",
          });
          this.remotePlayback.shift();
        }
      }
      if (this.remotePlayback.length > 0)
        this.playbackFrame = requestAnimationFrame(update);
    };
    this.playbackFrame = requestAnimationFrame(update);
  }

  private cancelPlaybackFrame(): void {
    if (this.playbackFrame === null) return;
    cancelAnimationFrame(this.playbackFrame);
    this.playbackFrame = null;
  }

  private isDesired(epoch: number): boolean {
    return (
      this.desiredConnected && !this.disposed && epoch === this.connectionEpoch
    );
  }

  private applyCaptureState(): void {
    setStreamCaptureEnabled(this.stream, this.captureEnabled);
  }

  private startInputMeter(stream: MediaStream): void {
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.65;
    source.connect(analyser);
    this.audioContext = context;
    const samples = new Float32Array(analyser.fftSize);
    const update = () => {
      if (this.disposed || analyser.context.state === "closed") return;
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += sample * sample;
      this.callbacks.onInputLevel(
        Math.min(1, Math.sqrt(sum / samples.length) * 5),
      );
      this.meterFrame = requestAnimationFrame(update);
    };
    void context.resume();
    update();
  }

  private async teardownConnection(notifyRuntime: boolean): Promise<void> {
    const pcIdToClose = this.activePcId;
    const targetSessionId = this.activeSessionId;
    const runtime = this.activeRuntime;
    this.activePcId = null;
    this.activeSessionId = null;
    this.activeRuntime = null;
    this.cancelHealthyTimer();
    if (this.meterFrame !== null) {
      cancelAnimationFrame(this.meterFrame);
      this.meterFrame = null;
    }
    this.callbacks.onInputLevel(0);
    const peer = this.peer;
    this.peer = null;
    if (peer) {
      peer.onconnectionstatechange = null;
      peer.ontrack = null;
      peer.close();
    }
    if (this.dataChannel) {
      this.dataChannel.onmessage = null;
      this.dataChannel.close();
      this.dataChannel = null;
    }
    if (this.output) {
      this.output.pause();
      this.output.srcObject = null;
      this.output = null;
    }
    if (this.stream) {
      stopStream(this.stream);
      this.stream = null;
    }
    const audioContext = this.audioContext;
    this.audioContext = null;
    if (audioContext) await audioContext.close().catch(() => undefined);
    if (notifyRuntime && peer && targetSessionId && pcIdToClose && runtime) {
      await runtimeFetchWithConnection(
        runtime,
        `/v1/sessions/${targetSessionId}/webrtc?pc_id=${encodeURIComponent(pcIdToClose)}`,
        { method: "DELETE" },
      ).catch(() => undefined);
    }
  }
}

export function parsePlaybackMarker(data: unknown): PlaybackMarker | null {
  try {
    const parsed =
      typeof data === "string" ? (JSON.parse(data) as unknown) : data;
    if (!isRecord(parsed) || parsed.type !== "chatwaifu.playback_segment")
      return null;
    if (parsed.phase !== "started" && parsed.phase !== "buffered") return null;
    if (
      typeof parsed.generation_id !== "string" ||
      typeof parsed.stream_id !== "string" ||
      typeof parsed.segment_id !== "string" ||
      typeof parsed.duration_ms !== "number" ||
      !Number.isInteger(parsed.duration_ms) ||
      parsed.duration_ms < 0
    )
      return null;
    return {
      phase: parsed.phase,
      generationId: parsed.generation_id,
      streamId: parsed.stream_id,
      segmentId: parsed.segment_id,
      durationMs: parsed.duration_ms,
    };
  } catch {
    return null;
  }
}

export function setStreamCaptureEnabled(
  stream: Pick<MediaStream, "getAudioTracks"> | null,
  enabled: boolean,
): void {
  for (const track of stream?.getAudioTracks() ?? []) track.enabled = enabled;
}

export function waitForIceGathering(
  peer: RTCPeerConnection,
  timeoutMs: number,
): Promise<void> {
  if (peer.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const timer = window.setTimeout(finish, timeoutMs);
    peer.addEventListener("icegatheringstatechange", onStateChange);
    function onStateChange() {
      if (peer.iceGatheringState === "complete") finish();
    }
    function finish() {
      window.clearTimeout(timer);
      peer.removeEventListener("icegatheringstatechange", onStateChange);
      resolve();
    }
  });
}

function remoteReceipt(
  segment: RemotePlaybackSegment,
  phase: "started" | "progress" | "stopped",
  playedPtsMs: number,
): PlaybackAckReceipt {
  return {
    phase,
    generationId: segment.generationId,
    streamId: segment.streamId,
    segmentId: segment.segmentId,
    playedPtsMs,
    bufferedMs: Math.max(0, segment.durationMs - playedPtsMs),
    clientClockMs: clientClockMs(),
    transport: "webrtc",
  };
}

function clientClockMs(): number {
  return Math.max(0, Math.round(performance.now()));
}

function stopStream(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === "AbortError"
    : error instanceof Error && error.name === "AbortError";
}

function isPermissionError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "NotAllowedError";
}

function isNonRetryableError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { nonRetryable?: boolean }).nonRetryable === true
  );
}

function voiceErrorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "麦克风权限被拒绝。请在浏览器地址栏允许麦克风后重试。";
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return "没有找到可用的麦克风。";
  }
  if (error instanceof DOMException && error.name === "NotReadableError") {
    return "麦克风暂时不可用，可能正被其他应用占用。";
  }
  return error instanceof Error ? error.message : "语音连接失败。";
}
