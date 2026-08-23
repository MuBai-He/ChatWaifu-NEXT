import { RUNTIME_URL } from "./runtimeClient";

export type VoiceConnectionState =
  | "unsupported"
  | "disconnected"
  | "requesting"
  | "connecting"
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
  onError: (message: string) => void;
}

export class BrowserVoiceClient {
  private peer: RTCPeerConnection | null = null;
  private stream: MediaStream | null = null;
  private output: HTMLAudioElement | null = null;
  private audioContext: AudioContext | null = null;
  private meterFrame: number | null = null;
  private captureEnabled = false;
  private disposed = false;

  constructor(private readonly callbacks: VoiceClientCallbacks) {}

  static supported(): boolean {
    return (
      typeof navigator !== "undefined" &&
      typeof navigator.mediaDevices?.getUserMedia === "function" &&
      typeof RTCPeerConnection === "function"
    );
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
    if (!BrowserVoiceClient.supported()) {
      this.callbacks.onStateChange("unsupported");
      return;
    }
    await this.disconnect(sessionId);
    this.callbacks.onStateChange("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      if (this.disposed) {
        stopStream(stream);
        return;
      }
      this.stream = stream;
      this.applyCaptureState();
      this.startInputMeter(stream);

      const output = document.createElement("audio");
      output.autoplay = true;
      output.setAttribute("playsinline", "true");
      this.output = output;

      const peer = new RTCPeerConnection({ iceServers: [] });
      this.peer = peer;
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
        if (this.disposed || peer !== this.peer) return;
        if (peer.connectionState === "connected") {
          this.callbacks.onStateChange("connected");
        } else if (
          peer.connectionState === "failed" ||
          peer.connectionState === "disconnected"
        ) {
          this.callbacks.onStateChange("failed");
        }
      };

      this.callbacks.onStateChange("connecting");
      await peer.setLocalDescription(await peer.createOffer());
      await waitForIceGathering(peer, 5_000);
      const local = peer.localDescription;
      if (!local) throw new Error("浏览器没有生成 WebRTC offer。");
      const response = await fetch(
        `${RUNTIME_URL}/v1/sessions/${sessionId}/webrtc/offer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sdp: local.sdp, type: "offer" }),
        },
      );
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(body?.detail ?? `WebRTC 连接失败 (${response.status})`);
      }
      const answer = (await response.json()) as RTCSessionDescriptionInit;
      await peer.setRemoteDescription(answer);
    } catch (error: unknown) {
      this.callbacks.onStateChange("failed");
      this.callbacks.onError(voiceErrorMessage(error));
      await this.disconnect(sessionId);
      throw error;
    }
  }

  async disconnect(sessionId?: string): Promise<void> {
    if (this.meterFrame !== null) {
      cancelAnimationFrame(this.meterFrame);
      this.meterFrame = null;
    }
    this.callbacks.onInputLevel(0);
    this.peer?.close();
    this.peer = null;
    if (this.output) {
      this.output.pause();
      this.output.srcObject = null;
      this.output = null;
    }
    if (this.stream) {
      stopStream(this.stream);
      this.stream = null;
    }
    if (this.audioContext) {
      await this.audioContext.close().catch(() => undefined);
      this.audioContext = null;
    }
    if (!this.disposed) this.callbacks.onStateChange("disconnected");
    if (sessionId) {
      void fetch(`${RUNTIME_URL}/v1/sessions/${sessionId}/webrtc`, {
        method: "DELETE",
      }).catch(() => undefined);
    }
  }

  async dispose(sessionId?: string): Promise<void> {
    this.disposed = true;
    await this.disconnect(sessionId);
  }

  setCaptureEnabled(enabled: boolean): void {
    this.captureEnabled = enabled;
    this.applyCaptureState();
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

function stopStream(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop();
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === "AbortError"
    : error instanceof Error && error.name === "AbortError";
}

function voiceErrorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "麦克风权限被拒绝。请在浏览器地址栏允许麦克风后重试。";
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return "没有找到可用的麦克风。";
  }
  return error instanceof Error ? error.message : "语音连接失败。";
}
