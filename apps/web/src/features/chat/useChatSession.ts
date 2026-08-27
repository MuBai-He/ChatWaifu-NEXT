import { parseEventEnvelope } from "@chatwaifu/protocol";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  RUNTIME_URL,
  RUNTIME_WS_URL,
  acknowledgePlayback,
  createSession,
  getCharacters,
  getHealth,
  getMemory,
  getMessages,
  getSession,
  getTtsProviders,
  interrupt,
  resetSession,
  selectTtsProvider,
  sendCharacterInteraction,
  submitText,
} from "./runtimeClient";
import type {
  AudioPayload,
  AvatarCuePayload,
  CharacterProfile,
  ChatMessage,
  MemoryItem,
  RuntimeEvent,
  RuntimeHealth,
  TtsProviderSnapshot,
} from "./types";
import {
  GenerationAudioPlayer,
  type AudioPlaybackItem,
  type PlaybackPosition,
  type PlaybackStopReason,
} from "./audioPlayer";
import { PlaybackAckReporter } from "./playbackAckReporter";
import {
  SubtitlePlaybackTracker,
  type SubtitlePlaybackProgress,
} from "./subtitlePlayback";
import { StreamingTextProjector } from "./streamingTextProjector";
import { useChatAvatar } from "./useChatAvatar";
import { useVoiceInput } from "./useVoiceInput";

const SESSION_KEY = "chatwaifu.next.session_id";

export type ChatSessionOptions = {
  playbackEnabled?: boolean;
};

export function useChatSession({
  playbackEnabled = true,
}: ChatSessionOptions = {}) {
  const avatar = useChatAvatar();
  const {
    applyCue,
    invalidateGeneration,
    resetAvatar,
    startLipSync,
    stopLipSync,
  } = avatar;
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [character, setCharacter] = useState<CharacterProfile | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [connection, setConnection] = useState<
    "connecting" | "connected" | "offline"
  >("connecting");
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [ttsProviders, setTtsProviders] = useState<TtsProviderSnapshot[]>([]);
  const [ttsProviderId, setTtsProviderId] = useState("qwen3_tts_mlx");
  const [ttsSwitching, setTtsSwitching] = useState(false);
  const [voiceActivity, setVoiceActivity] = useState<
    "idle" | "listening" | "transcribing" | "thinking"
  >("idle");
  const [voiceTranscript, setVoiceTranscript] = useState<string | null>(null);
  const [subtitlePlayback, setSubtitlePlayback] =
    useState<SubtitlePlaybackProgress | null>(null);
  const activeGeneration = useRef<string | null>(null);
  const voiceConnected = useRef(false);
  const audioPlayer = useRef<GenerationAudioPlayer | null>(null);
  const playbackReporter = useRef<PlaybackAckReporter | null>(null);
  const textProjector = useRef<StreamingTextProjector | null>(null);
  const subtitlePlaybackTracker = useRef(new SubtitlePlaybackTracker());

  const setAvatarState = useCallback(
    (name: "idle" | "listening" | "thinking") => {
      applyCue({
        cue_id: crypto.randomUUID(),
        kind: "state",
        name,
        priority: 95,
      });
    },
    [applyCue],
  );
  const onVoiceConnectionChange = useCallback(
    (connected: boolean) => {
      voiceConnected.current = connected;
      if (!connected) {
        setVoiceActivity("idle");
        setAvatarState("idle");
      }
    },
    [setAvatarState],
  );
  const getPlaybackReporter = useCallback(() => {
    if (!playbackReporter.current && sessionId) {
      playbackReporter.current = new PlaybackAckReporter({
        send: (receipt) => acknowledgePlayback(sessionId, receipt),
        onError: () => setError("播放进度同步失败；本次已听内容可能不完整。"),
      });
    }
    return playbackReporter.current;
  }, [sessionId]);

  const onVoicePlaybackReceipt = useCallback(
    (receipt: Parameters<typeof acknowledgePlayback>[1]) => {
      const progress = subtitlePlaybackTracker.current.report(receipt);
      if (progress) setSubtitlePlayback(progress);
      getPlaybackReporter()?.report(receipt);
      if (receipt.phase === "started") startLipSync();
      if (receipt.phase === "stopped" || receipt.phase === "queue_cleared")
        stopLipSync();
    },
    [getPlaybackReporter, startLipSync, stopLipSync],
  );

  const voice = useVoiceInput({
    sessionId,
    onError: setError,
    onConnectionChange: onVoiceConnectionChange,
    onPlaybackReceipt: onVoicePlaybackReceipt,
  });
  const stopRemotePlayback = voice.stopRemotePlayback;

  const reportElementPlayback = useCallback(
    (
      item: AudioPlaybackItem,
      phase: "started" | "progress" | "stopped" | "queue_cleared",
      position: PlaybackPosition,
      reason?: PlaybackStopReason,
    ) => {
      const receipt = {
        phase,
        generationId: item.generationId,
        streamId: item.streamId,
        segmentId: item.segmentId,
        playedPtsMs: position.playedPtsMs,
        bufferedMs: position.bufferedMs,
        clientClockMs: position.clientClockMs,
        transport: "audio_element",
        reason,
      } as const;
      const progress = subtitlePlaybackTracker.current.report(receipt);
      if (progress) setSubtitlePlayback(progress);
      getPlaybackReporter()?.report(receipt);
    },
    [getPlaybackReporter],
  );

  const getAudioPlayer = useCallback(() => {
    if (!playbackEnabled) return null;
    if (!audioPlayer.current && typeof Audio !== "undefined") {
      audioPlayer.current = new GenerationAudioPlayer((url) => new Audio(url), {
        isGenerationActive: (generationId) =>
          generationId === activeGeneration.current,
        onPlaybackStart: (item, position) => {
          startLipSync();
          reportElementPlayback(item, "started", position);
        },
        onPlaybackProgress: (item, position) =>
          reportElementPlayback(item, "progress", position),
        onPlaybackStop: (item, position, reason) => {
          stopLipSync();
          reportElementPlayback(item, "stopped", position, reason);
        },
        onQueueCleared: (item) =>
          reportElementPlayback(
            item,
            "queue_cleared",
            {
              playedPtsMs: 0,
              bufferedMs: 0,
              clientClockMs: Math.round(performance.now()),
            },
            "queue_cleared",
          ),
        onPlaybackError: setError,
      });
    }
    return audioPlayer.current;
  }, [playbackEnabled, reportElementPlayback, startLipSync, stopLipSync]);

  const stopAudio = useCallback(
    (generationId?: string) => {
      audioPlayer.current?.stop();
      stopRemotePlayback(generationId);
      stopLipSync();
      if (generationId) invalidateGeneration(generationId);
    },
    [invalidateGeneration, stopLipSync, stopRemotePlayback],
  );

  const getTextProjector = useCallback(() => {
    if (!textProjector.current) {
      textProjector.current = new StreamingTextProjector({
        onReveal: (generationId, text) => {
          if (generationId !== activeGeneration.current) return;
          setMessages((current) =>
            current.map((message) =>
              message.generationId === generationId
                ? { ...message, text: message.text + text }
                : message,
            ),
          );
        },
        onComplete: (generationId) => {
          if (generationId !== activeGeneration.current) return;
          setMessages((current) =>
            current.map((message) =>
              message.generationId === generationId
                ? { ...message, pending: false }
                : message,
            ),
          );
        },
      });
    }
    return textProjector.current;
  }, []);

  const stopText = useCallback((generationId?: string) => {
    if (!generationId) return;
    textProjector.current?.cancel(generationId);
    setMessages((current) =>
      current.map((message) =>
        message.generationId === generationId
          ? { ...message, pending: false }
          : message,
      ),
    );
  }, []);

  const handleEvent = useCallback(
    (event: RuntimeEvent) => {
      const generationId = event.generation_id ?? undefined;
      switch (event.event_type) {
        case "user.speech_started": {
          const previousGeneration = activeGeneration.current;
          if (previousGeneration) {
            stopText(previousGeneration);
            stopAudio(previousGeneration);
          }
          setVoiceActivity("listening");
          setAvatarState("listening");
          setVoiceTranscript(null);
          break;
        }
        case "user.speech_stopped": {
          setVoiceActivity("transcribing");
          setAvatarState("thinking");
          break;
        }
        case "user.transcript_partial": {
          setVoiceTranscript(payloadText(event.payload.text));
          break;
        }
        case "user.transcript_final": {
          setVoiceTranscript(payloadText(event.payload.text));
          setVoiceActivity("thinking");
          setAvatarState("thinking");
          break;
        }
        case "user.turn_committed": {
          setMessages((current) => [
            ...current,
            {
              id: event.turn_id ?? event.event_id,
              role: "user",
              text: payloadText(event.payload.text),
            },
          ]);
          break;
        }
        case "assistant.generation_started": {
          if (!generationId) break;
          setVoiceActivity("thinking");
          setAvatarState("thinking");
          activeGeneration.current = generationId;
          setSubtitlePlayback(
            subtitlePlaybackTracker.current.start(generationId),
          );
          getTextProjector().start(generationId);
          setMessages((current) => [
            ...current,
            {
              id: generationId,
              role: "assistant",
              text: "",
              generationId,
              pending: true,
            },
          ]);
          break;
        }
        case "assistant.text_delta": {
          if (!generationId || generationId !== activeGeneration.current) break;
          getTextProjector().push(
            generationId,
            payloadText(event.payload.text),
          );
          break;
        }
        case "assistant.audio_chunk_queued": {
          if (!generationId || generationId !== activeGeneration.current) break;
          const payload = event.payload as unknown as AudioPayload;
          const item: AudioPlaybackItem = {
            generationId,
            streamId: payload.stream_id,
            segmentId: payload.segment_id,
            segmentIndex: payload.segment_index,
            text: payload.text,
            durationMs: payload.duration_ms,
            url: `${RUNTIME_URL}${payload.url}`,
          };
          const progress =
            subtitlePlaybackTracker.current.registerSegment(item);
          if (progress) setSubtitlePlayback(progress);
          if (voiceConnected.current) break;
          getAudioPlayer()?.enqueue(item);
          break;
        }
        case "avatar.cue_emitted": {
          const payload = event.payload as unknown as AvatarCuePayload;
          if (!generationId || generationId === activeGeneration.current)
            applyCue(payload.cue);
          break;
        }
        case "assistant.generation_completed": {
          if (!generationId || generationId !== activeGeneration.current) break;
          getTextProjector().complete(generationId);
          void getMemory().then(setMemories);
          setVoiceActivity("idle");
          setAvatarState("idle");
          setVoiceTranscript(null);
          break;
        }
        case "assistant.generation_cancelled":
        case "conversation.interrupted": {
          if (generationId) {
            stopText(generationId);
            stopAudio(generationId);
          }
          setMessages((current) =>
            current.filter(
              (message) =>
                message.generationId !== generationId ||
                message.text.length > 0,
            ),
          );
          activeGeneration.current = null;
          setVoiceActivity("idle");
          setAvatarState("idle");
          break;
        }
        case "system.error_raised": {
          const nested = event.payload.error as
            { message?: string } | undefined;
          setError(nested?.message ?? "Runtime 生成失败。");
          if (nested?.message?.includes("语音转写")) {
            setVoiceActivity("idle");
            setAvatarState("idle");
          }
          break;
        }
      }
    },
    [
      applyCue,
      getAudioPlayer,
      getTextProjector,
      setAvatarState,
      stopAudio,
      stopText,
    ],
  );

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const connect = (resolvedSessionId: string) => {
      if (disposed) return;
      socket = new WebSocket(
        `${RUNTIME_WS_URL}/v1/events?session_id=${resolvedSessionId}`,
      );
      socket.onopen = () => setConnection("connected");
      socket.onmessage = (message) => {
        const raw = JSON.parse(String(message.data)) as unknown;
        if (typeof raw !== "object" || raw === null || !("event_id" in raw))
          return;
        try {
          handleEvent(parseEventEnvelope(raw) as RuntimeEvent);
        } catch {
          const eventType =
            "event_type" in raw && typeof raw.event_type === "string"
              ? raw.event_type
              : "unknown";
          setError(`收到无法识别的 Runtime 事件（${eventType}），已安全忽略。`);
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        setConnection("connecting");
        reconnectTimer = window.setTimeout(
          () => connect(resolvedSessionId),
          1200,
        );
      };
      socket.onerror = () => setConnection("offline");
    };

    const initialize = async () => {
      try {
        const [resolvedHealth, characters] = await Promise.all([
          getHealth(),
          getCharacters(),
        ]);
        if (disposed) return;
        setHealth(resolvedHealth);
        const selected = characters[0];
        if (!selected) throw new Error("没有安装角色 manifest。");
        setCharacter(selected);
        let session = null;
        const saved = localStorage.getItem(SESSION_KEY);
        if (saved) session = await getSession(saved).catch(() => null);
        if (!session || session.state !== "ready")
          session = await createSession(selected.character_id);
        if (disposed) return;
        localStorage.setItem(SESSION_KEY, session.session_id);
        setSessionId(session.session_id);
        const [history, savedMemories, availableTts] = await Promise.all([
          getMessages(session.session_id),
          getMemory(),
          getTtsProviders(session.session_id).catch(() => []),
        ]);
        if (disposed) return;
        setMessages(
          history.map((message) => ({
            id: message.turn_id,
            role: message.role,
            text: message.committed_text,
          })),
        );
        setMemories(savedMemories);
        setTtsProviders(availableTts);
        const selectedTts = availableTts.find((provider) => provider.selected);
        if (selectedTts) setTtsProviderId(selectedTts.provider_id);
        connect(session.session_id);
      } catch (runtimeError: unknown) {
        if (disposed) return;
        setConnection("offline");
        setError(
          runtimeError instanceof Error
            ? runtimeError.message
            : "Runtime 不可用",
        );
      }
    };
    void initialize();
    return () => {
      disposed = true;
      socket?.close();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      stopAudio(activeGeneration.current ?? undefined);
      audioPlayer.current?.dispose();
      audioPlayer.current = null;
      playbackReporter.current?.dispose();
      playbackReporter.current = null;
      textProjector.current?.dispose();
      textProjector.current = null;
    };
  }, [handleEvent, stopAudio]);

  const send = useCallback(
    async (text: string) => {
      if (!sessionId || !text.trim()) return;
      getAudioPlayer()?.prime();
      if (connection !== "connected") {
        setError("Runtime 事件通道尚未连接，请稍等片刻再发送。");
        return;
      }
      setError(null);
      const previousGeneration = activeGeneration.current;
      if (previousGeneration) {
        stopText(previousGeneration);
        stopAudio(previousGeneration);
      }
      try {
        await submitText(sessionId, text.trim());
      } catch (sendError: unknown) {
        setError(
          sendError instanceof Error ? sendError.message : "消息发送失败",
        );
      }
    },
    [connection, getAudioPlayer, sessionId, stopAudio, stopText],
  );

  const interruptActive = useCallback(async () => {
    if (!sessionId) return;
    const generationId = activeGeneration.current;
    if (generationId) {
      stopText(generationId);
      stopAudio(generationId);
    }
    activeGeneration.current = null;
    await interrupt(sessionId).catch((interruptError: unknown) => {
      setError(
        interruptError instanceof Error ? interruptError.message : "打断失败",
      );
    });
  }, [sessionId, stopAudio, stopText]);

  const changeTtsProvider = useCallback(
    async (providerId: string) => {
      if (!sessionId || ttsSwitching || providerId === ttsProviderId) return;
      setTtsSwitching(true);
      setError(null);
      const generationId = activeGeneration.current;
      if (generationId) {
        stopText(generationId);
        stopAudio(generationId);
        activeGeneration.current = null;
      }
      try {
        const selected = await selectTtsProvider(sessionId, providerId);
        setTtsProviderId(selected.provider_id);
        setTtsProviders((current) =>
          current.map((provider) => ({
            ...provider,
            selected: provider.provider_id === selected.provider_id,
          })),
        );
        setHealth((current) =>
          current
            ? {
                ...current,
                providers: { ...current.providers, tts: selected.provider_id },
              }
            : current,
        );
      } catch (selectionError: unknown) {
        setError(
          selectionError instanceof Error
            ? selectionError.message
            : "切换语音模型失败",
        );
      } finally {
        setTtsSwitching(false);
      }
    },
    [sessionId, stopAudio, stopText, ttsProviderId, ttsSwitching],
  );

  const resetAll = useCallback(async (): Promise<boolean> => {
    if (!sessionId || resetting) return false;
    setResetting(true);
    setError(null);
    const generationId = activeGeneration.current;
    stopText(generationId ?? undefined);
    stopAudio(generationId ?? undefined);
    activeGeneration.current = null;
    subtitlePlaybackTracker.current.reset();
    setSubtitlePlayback(null);
    try {
      await resetSession(sessionId);
      setMessages([]);
      setMemories([]);
      resetAvatar();
      return true;
    } catch (resetError: unknown) {
      setError(resetError instanceof Error ? resetError.message : "重置失败");
      return false;
    } finally {
      setResetting(false);
    }
  }, [resetAvatar, resetting, sessionId, stopAudio, stopText]);

  const refreshMemories = useCallback(async () => {
    setMemories(await getMemory());
  }, []);

  const touch = useCallback(() => {
    getAudioPlayer()?.prime();
    avatar.touch();
    if (sessionId) {
      void sendCharacterInteraction(sessionId, "avatar_touch").catch(
        (interactionError: unknown) => {
          setError(
            interactionError instanceof Error
              ? interactionError.message
              : "角色互动同步失败",
          );
        },
      );
    }
  }, [avatar, getAudioPlayer, sessionId]);

  return {
    ...avatar,
    touch,
    health,
    character,
    sessionId,
    messages,
    memories,
    connection,
    error,
    resetting,
    ttsProviders,
    ttsProviderId,
    ttsSwitching,
    voiceState: voice.state,
    voiceConnected: voice.connected,
    voiceDevices: voice.devices,
    voiceDeviceId: voice.deviceId,
    voiceInputLevel: voice.inputLevel,
    voiceActivationMode: voice.activationMode,
    voiceTransmitting: voice.transmitting,
    voiceActivity,
    voiceTranscript,
    subtitlePlayback,
    setVoiceDeviceId: voice.setDeviceId,
    setVoiceActivationMode: voice.setActivationMode,
    beginPushToTalk: voice.beginPushToTalk,
    endPushToTalk: voice.endPushToTalk,
    refreshVoiceDevices: voice.refreshDevices,
    toggleVoice: voice.toggle,
    changeTtsProvider,
    send,
    interruptActive,
    resetAll,
    refreshMemories,
  };
}

function payloadText(value: unknown): string {
  return typeof value === "string" ? value : "";
}
