import { parseEventEnvelope, type DomainEvent } from "@chatwaifu/protocol";

import {
  ttsStreamMessageSchema,
  type TtsStreamMessage,
} from "./runtime-client/contracts";
import { getSessionEvents } from "./runtime-client/sessionsClient";
import {
  acquireWsTicket,
  resolveRuntimeConnection,
  runtimeWebSocketUrlFromConnection,
} from "./runtimeEndpoint";

export const RUNTIME_EVENT_NOTIFICATION = "chatwaifu:runtime-event";
export const RUNTIME_CONNECTION_NOTIFICATION = "chatwaifu:runtime-connection";
export type RuntimeConnectionState = "connecting" | "connected" | "offline";
export type RuntimeConnectionNotification = {
  sessionId: string;
  state: RuntimeConnectionState;
};

interface RuntimeSocketCallbacks {
  onConnection: (state: RuntimeConnectionState) => void;
  onEvent: (event: DomainEvent) => void;
  onAudio: (message: TtsStreamMessage) => void;
  onProtocolError: (message: string) => void;
}

export class RuntimeSocketClient {
  private eventSocket: WebSocket | null = null;
  private audioSocket: WebSocket | null = null;
  private eventReconnectTimer: number | null = null;
  private audioReconnectTimer: number | null = null;
  private sessionId: string | null = null;
  private lastSequence = 0;
  private eventPipeline: Promise<void> = Promise.resolve();
  private disposed = false;
  private connectionEpoch = 0;

  constructor(
    private readonly callbacks: RuntimeSocketCallbacks,
    private readonly audioEnabled: boolean,
    private readonly reconnectDelayMs = 1_200,
  ) {}

  start(sessionId: string, afterSequence = 0): void {
    const epoch = ++this.connectionEpoch;
    this.stopSockets();
    this.disposed = false;
    this.sessionId = sessionId;
    this.lastSequence = Math.max(0, afterSequence);
    this.eventPipeline = Promise.resolve();
    this.notifyConnection("connecting");
    void this.connectEvents(false, epoch);
    if (this.audioEnabled) void this.connectAudio(false, epoch);
  }

  stop(): void {
    this.connectionEpoch += 1;
    this.disposed = true;
    this.sessionId = null;
    this.stopSockets();
  }

  restartFrom(afterSequence: number): void {
    const sessionId = this.sessionId;
    if (!sessionId) return;
    this.start(sessionId, afterSequence);
  }

  private stopSockets(): void {
    if (this.eventReconnectTimer !== null) {
      window.clearTimeout(this.eventReconnectTimer);
      this.eventReconnectTimer = null;
    }
    if (this.audioReconnectTimer !== null) {
      window.clearTimeout(this.audioReconnectTimer);
      this.audioReconnectTimer = null;
    }
    const eventSocket = this.eventSocket;
    const audioSocket = this.audioSocket;
    this.eventSocket = null;
    this.audioSocket = null;
    eventSocket?.close();
    audioSocket?.close();
  }

  private async connectEvents(
    refreshEndpoint: boolean,
    epoch: number,
  ): Promise<void> {
    const sessionId = this.sessionId;
    if (!sessionId || !this.isCurrent(epoch, sessionId)) return;
    try {
      const conn = await resolveRuntimeConnection(refreshEndpoint);
      if (!this.isCurrent(epoch, sessionId)) return;
      const baseUrl = runtimeWebSocketUrlFromConnection(conn);
      const ticket = await acquireWsTicket("events", conn, sessionId);
      if (!this.isCurrent(epoch, sessionId)) return;
      const socket = new WebSocket(
        `${baseUrl}/v1/events?session_id=${encodeURIComponent(sessionId)}&after_sequence=${this.lastSequence}&ticket=${encodeURIComponent(ticket)}`,
      );
      if (!this.isCurrent(epoch, sessionId)) {
        socket.close();
        return;
      }
      this.eventSocket = socket;
      socket.onopen = () => {
        if (this.isEventSocketCurrent(socket, epoch, sessionId))
          this.notifyConnection("connected");
      };
      socket.onmessage = (message) => {
        if (!this.isEventSocketCurrent(socket, epoch, sessionId)) return;
        this.eventPipeline = this.eventPipeline
          .then(() => this.consumeEvent(message.data, sessionId, epoch, socket))
          .catch((error: unknown) => {
            if (!this.isEventSocketCurrent(socket, epoch, sessionId)) return;
            this.callbacks.onProtocolError(
              messageText(error, "Runtime 事件恢复失败。"),
            );
            socket.close();
          });
      };
      socket.onerror = () => {
        if (this.isEventSocketCurrent(socket, epoch, sessionId))
          this.notifyConnection("offline");
      };
      socket.onclose = () => {
        if (!this.isEventSocketCurrent(socket, epoch, sessionId)) return;
        this.eventSocket = null;
        this.notifyConnection("connecting");
        this.scheduleEventReconnect(epoch, sessionId);
      };
    } catch (error: unknown) {
      if (!this.isCurrent(epoch, sessionId)) return;
      this.notifyConnection("offline");
      this.callbacks.onProtocolError(
        messageText(error, "Runtime 事件连接失败。"),
      );
      this.scheduleEventReconnect(epoch, sessionId);
    }
  }

  private async connectAudio(
    refreshEndpoint: boolean,
    epoch: number,
  ): Promise<void> {
    const sessionId = this.sessionId;
    if (!sessionId || !this.audioEnabled || !this.isCurrent(epoch, sessionId))
      return;
    try {
      const conn = await resolveRuntimeConnection(refreshEndpoint);
      if (!this.isCurrent(epoch, sessionId)) return;
      const baseUrl = runtimeWebSocketUrlFromConnection(conn);
      const ticket = await acquireWsTicket("audio", conn, sessionId);
      if (!this.isCurrent(epoch, sessionId)) return;
      const socket = new WebSocket(
        `${baseUrl}/v1/audio/stream?session_id=${encodeURIComponent(sessionId)}&ticket=${encodeURIComponent(ticket)}`,
      );
      if (!this.isCurrent(epoch, sessionId)) {
        socket.close();
        return;
      }
      this.audioSocket = socket;
      socket.onmessage = (message) => {
        if (this.isAudioSocketCurrent(socket, epoch, sessionId))
          this.consumeAudio(message.data);
      };
      socket.onclose = () => {
        if (!this.isAudioSocketCurrent(socket, epoch, sessionId)) return;
        this.audioSocket = null;
        this.scheduleAudioReconnect(epoch, sessionId);
      };
      socket.onerror = () => {
        if (this.isAudioSocketCurrent(socket, epoch, sessionId)) {
          this.audioSocket = null;
          this.scheduleAudioReconnect(epoch, sessionId);
        }
      };
    } catch {
      if (!this.isCurrent(epoch, sessionId)) return;
      this.scheduleAudioReconnect(epoch, sessionId);
    }
  }

  private async consumeEvent(
    data: unknown,
    sessionId: string,
    epoch: number,
    socket: WebSocket,
  ): Promise<void> {
    const payload = safelyParseJson(data);
    if (payload === null) {
      this.callbacks.onProtocolError(
        "收到无法解析的 Runtime 事件，已安全忽略。",
      );
      return;
    }
    const parsed = parseEventEnvelope(payload);
    const sequence = eventSequence(parsed);
    if (sequence !== null) {
      if (sequence <= this.lastSequence) return;
      if (sequence > this.lastSequence + 1)
        await this.replayGap(sessionId, sequence, epoch, socket);
      if (!this.isEventSocketCurrent(socket, epoch, sessionId)) return;
      if (sequence <= this.lastSequence) return;
      if (sequence !== this.lastSequence + 1)
        throw new EventSequenceGapError(this.lastSequence + 1, sequence);
    }
    this.deliverEvent(parsed, epoch, sessionId);
  }

  private async replayGap(
    sessionId: string,
    receivedSequence: number,
    epoch: number,
    socket: WebSocket,
  ): Promise<void> {
    while (
      this.isEventSocketCurrent(socket, epoch, sessionId) &&
      this.lastSequence + 1 < receivedSequence
    ) {
      const events = await getSessionEvents(sessionId, this.lastSequence);
      if (!this.isEventSocketCurrent(socket, epoch, sessionId)) return;
      if (!events.length)
        throw new EventSequenceGapError(
          this.lastSequence + 1,
          receivedSequence,
        );
      let advanced = false;
      for (const event of events) {
        const sequence = eventSequence(event);
        if (sequence === null || sequence <= this.lastSequence) continue;
        if (sequence !== this.lastSequence + 1)
          throw new EventSequenceGapError(this.lastSequence + 1, sequence);
        this.deliverEvent(event, epoch, sessionId);
        advanced = true;
      }
      if (!advanced)
        throw new EventSequenceGapError(
          this.lastSequence + 1,
          receivedSequence,
        );
    }
  }

  private deliverEvent(
    event: DomainEvent,
    epoch: number,
    sessionId: string,
  ): void {
    if (!this.isCurrent(epoch, sessionId)) return;
    this.callbacks.onEvent(event);
    const sequence = eventSequence(event);
    if (sequence !== null) this.lastSequence = sequence;
    window.dispatchEvent(
      new CustomEvent<DomainEvent>(RUNTIME_EVENT_NOTIFICATION, {
        detail: event,
      }),
    );
  }

  private consumeAudio(data: unknown): void {
    try {
      this.callbacks.onAudio(ttsStreamMessageSchema.parse(parseJson(data)));
    } catch {
      this.callbacks.onProtocolError(
        "收到无效的 Runtime 音频分片，已安全忽略。",
      );
    }
  }

  private isCurrent(epoch: number, sessionId: string): boolean {
    return (
      !this.disposed &&
      this.connectionEpoch === epoch &&
      this.sessionId === sessionId
    );
  }

  private notifyConnection(state: RuntimeConnectionState): void {
    this.callbacks.onConnection(state);
    const sessionId = this.sessionId;
    if (!sessionId) return;
    window.dispatchEvent(
      new CustomEvent<RuntimeConnectionNotification>(
        RUNTIME_CONNECTION_NOTIFICATION,
        { detail: { sessionId, state } },
      ),
    );
  }

  private isEventSocketCurrent(
    socket: WebSocket,
    epoch: number,
    sessionId: string,
  ): boolean {
    return this.isCurrent(epoch, sessionId) && this.eventSocket === socket;
  }

  private isAudioSocketCurrent(
    socket: WebSocket,
    epoch: number,
    sessionId: string,
  ): boolean {
    return this.isCurrent(epoch, sessionId) && this.audioSocket === socket;
  }

  private scheduleEventReconnect(epoch: number, sessionId: string): void {
    if (!this.isCurrent(epoch, sessionId)) return;
    if (this.eventReconnectTimer !== null)
      window.clearTimeout(this.eventReconnectTimer);
    this.eventReconnectTimer = window.setTimeout(() => {
      this.eventReconnectTimer = null;
      if (!this.isCurrent(epoch, sessionId)) return;
      this.notifyConnection("connecting");
      void this.eventPipeline.finally(() => this.connectEvents(true, epoch));
    }, this.reconnectDelayMs);
  }

  private scheduleAudioReconnect(epoch: number, sessionId: string): void {
    if (!this.isCurrent(epoch, sessionId)) return;
    if (this.audioReconnectTimer !== null)
      window.clearTimeout(this.audioReconnectTimer);
    this.audioReconnectTimer = window.setTimeout(() => {
      this.audioReconnectTimer = null;
      if (this.isCurrent(epoch, sessionId)) void this.connectAudio(true, epoch);
    }, this.reconnectDelayMs);
  }
}

class EventSequenceGapError extends Error {
  constructor(expected: number, received: number) {
    super(
      `Runtime 事件序列不连续：期待 ${expected}，实际收到 ${received}。已重新连接以恢复。`,
    );
  }
}

function parseJson(data: unknown): unknown {
  if (typeof data !== "string")
    throw new Error("WebSocket payload is not text");
  return JSON.parse(data) as unknown;
}

function safelyParseJson(data: unknown): unknown {
  try {
    return parseJson(data);
  } catch {
    return null;
  }
}

function eventSequence(event: DomainEvent): number | null {
  return typeof event.sequence === "number" ? event.sequence : null;
}

function messageText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
