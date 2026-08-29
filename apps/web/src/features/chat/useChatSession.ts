import type { DomainEvent } from "@chatwaifu/protocol";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { bootstrapChatSession } from "./chatSessionBootstrap";
import { PlaybackCoordinator } from "./playbackCoordinator";
import {
  initialRuntimeViewState,
  runtimeEventReducer,
} from "./runtimeEventReducer";
import {
  acknowledgePlayback,
  getMemory,
  sendCharacterInteraction,
} from "./runtimeClient";
import { audioPayloadSchema } from "./runtime-client/contracts";
import { runtimeAssetUrl } from "./runtimeEndpoint";
import { RuntimeSocketClient } from "./runtimeSocketClient";
import { StreamingTextProjector } from "./streamingTextProjector";
import type { SubtitlePlaybackProgress } from "./subtitlePlayback";
import type {
  CharacterProfile,
  MemoryItem,
  RuntimeHealth,
  TtsProviderSnapshot,
  TtsStreamMessage,
} from "./types";
import { useChatAvatar } from "./useChatAvatar";
import { useChatSessionCommands } from "./useChatSessionCommands";
import { useVoiceInput } from "./useVoiceInput";

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
  const [view, dispatch] = useReducer(
    runtimeEventReducer,
    initialRuntimeViewState,
  );
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [character, setCharacter] = useState<CharacterProfile | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [eventCursor, setEventCursor] = useState<number | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [connection, setConnection] = useState<
    "connecting" | "connected" | "offline"
  >("connecting");
  const [resetting, setResetting] = useState(false);
  const [ttsProviders, setTtsProviders] = useState<TtsProviderSnapshot[]>([]);
  const [ttsProviderId, setTtsProviderId] = useState("");
  const [ttsSwitching, setTtsSwitching] = useState(false);
  const [subtitlePlayback, setSubtitlePlayback] =
    useState<SubtitlePlaybackProgress | null>(null);
  const activeGeneration = useRef<string | null>(null);
  const voiceConnected = useRef(false);
  const playbackCoordinator = useRef<PlaybackCoordinator | null>(null);
  const socketClient = useRef<RuntimeSocketClient | null>(null);
  const textProjector = useRef<StreamingTextProjector | null>(null);

  const setError = useCallback((error: string | null) => {
    dispatch({ type: "set_error", error });
  }, []);

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
      playbackCoordinator.current?.setRemoteConnected(connected);
      if (!connected) {
        dispatch({ type: "voice_status", activity: "idle" });
        setAvatarState("idle");
      }
    },
    [setAvatarState],
  );

  const onVoicePlaybackReceipt = useCallback(
    (receipt: Parameters<typeof acknowledgePlayback>[1]) => {
      playbackCoordinator.current?.reportRemoteReceipt(receipt);
    },
    [],
  );

  const voice = useVoiceInput({
    sessionId,
    onError: setError,
    onConnectionChange: onVoiceConnectionChange,
    onPlaybackReceipt: onVoicePlaybackReceipt,
  });
  const { stopRemotePlayback } = voice;

  const getPlaybackCoordinator = useCallback(() => {
    if (!playbackCoordinator.current && sessionId) {
      playbackCoordinator.current = new PlaybackCoordinator({
        enabled: playbackEnabled,
        isGenerationActive: (generationId) =>
          generationId === activeGeneration.current,
        sendReceipt: (receipt) => acknowledgePlayback(sessionId, receipt),
        stopRemotePlayback,
        onSubtitle: setSubtitlePlayback,
        onError: setError,
        onLipSyncStart: startLipSync,
        onLipSyncStop: stopLipSync,
      });
      playbackCoordinator.current.setRemoteConnected(voiceConnected.current);
    }
    return playbackCoordinator.current;
  }, [
    playbackEnabled,
    sessionId,
    setError,
    startLipSync,
    stopLipSync,
    stopRemotePlayback,
  ]);

  const getTextProjector = useCallback(() => {
    if (!textProjector.current) {
      textProjector.current = new StreamingTextProjector({
        onReveal: (generationId, text) => {
          if (generationId !== activeGeneration.current) return;
          dispatch({ type: "text_revealed", generationId, text });
        },
        onComplete: (generationId) => {
          if (generationId !== activeGeneration.current) return;
          dispatch({ type: "text_completed", generationId });
        },
      });
    }
    return textProjector.current;
  }, []);

  const stopText = useCallback((generationId?: string) => {
    if (!generationId) return;
    textProjector.current?.cancel(generationId);
    dispatch({ type: "text_completed", generationId });
  }, []);

  const stopAudio = useCallback(
    (generationId?: string) => {
      if (playbackCoordinator.current) {
        playbackCoordinator.current.stop(generationId);
      } else {
        stopRemotePlayback(generationId);
        stopLipSync();
      }
      if (generationId) invalidateGeneration(generationId);
    },
    [invalidateGeneration, stopLipSync, stopRemotePlayback],
  );

  const handleEvent = useCallback(
    (event: DomainEvent) => {
      const generationId = event.generation_id ?? undefined;
      if (
        (event.event_type === "assistant.generation_completed" ||
          event.event_type === "assistant.generation_cancelled" ||
          event.event_type === "conversation.interrupted") &&
        generationId &&
        generationId !== activeGeneration.current
      )
        return;

      dispatch({ type: "runtime_event", event });
      switch (event.event_type) {
        case "session.data_reset": {
          const previousGeneration = activeGeneration.current;
          if (previousGeneration) {
            stopText(previousGeneration);
            stopAudio(previousGeneration);
          }
          activeGeneration.current = null;
          playbackCoordinator.current?.resetSubtitles();
          setSubtitlePlayback(null);
          setMemories([]);
          resetAvatar();
          break;
        }
        case "user.speech_started": {
          const previousGeneration = activeGeneration.current;
          if (previousGeneration) {
            stopText(previousGeneration);
            stopAudio(previousGeneration);
          }
          setAvatarState("listening");
          break;
        }
        case "user.speech_stopped":
        case "user.transcript_final":
          setAvatarState("thinking");
          break;
        case "voice.utterance_ignored":
          setAvatarState("idle");
          break;
        case "assistant.generation_started": {
          if (!generationId) break;
          const previousGeneration = activeGeneration.current;
          if (previousGeneration && previousGeneration !== generationId) {
            stopText(previousGeneration);
            stopAudio(previousGeneration);
          }
          activeGeneration.current = generationId;
          setAvatarState("thinking");
          getPlaybackCoordinator()?.startGeneration(generationId);
          getTextProjector().start(generationId);
          break;
        }
        case "assistant.text_delta":
          if (generationId === activeGeneration.current) {
            getTextProjector().push(
              generationId,
              payloadText(event.payload.text),
            );
          }
          break;
        case "assistant.audio_chunk_queued": {
          if (!generationId || generationId !== activeGeneration.current) break;
          const parsed = audioPayloadSchema.safeParse(event.payload);
          if (!parsed.success) {
            setError("Runtime 返回了无效的音频队列事件，已安全忽略。");
            break;
          }
          getPlaybackCoordinator()?.registerQueuedAudio(
            {
              generationId,
              streamId: parsed.data.stream_id,
              segmentId: parsed.data.segment_id,
              segmentIndex: parsed.data.segment_index,
              text: parsed.data.text,
              durationMs: parsed.data.duration_ms,
              url: runtimeAssetUrl(parsed.data.url),
            },
            parsed.data.streamed_live === true,
          );
          break;
        }
        case "avatar.cue_emitted":
          if (!generationId || generationId === activeGeneration.current) {
            applyCue(event.payload.cue);
          }
          break;
        case "assistant.generation_completed":
          if (!generationId || generationId !== activeGeneration.current) break;
          getTextProjector().complete(generationId);
          void getMemory()
            .then(setMemories)
            .catch(() => undefined);
          setAvatarState("idle");
          break;
        case "assistant.generation_cancelled":
        case "conversation.interrupted":
          if (generationId) {
            stopText(generationId);
            stopAudio(generationId);
          }
          activeGeneration.current = null;
          setAvatarState("idle");
          break;
        case "system.error_raised": {
          const nested = event.payload.error;
          if (
            typeof nested === "object" &&
            nested !== null &&
            "message" in nested &&
            typeof nested.message === "string" &&
            nested.message.includes("语音转写")
          )
            setAvatarState("idle");
          break;
        }
      }
    },
    [
      applyCue,
      getPlaybackCoordinator,
      getTextProjector,
      resetAvatar,
      setAvatarState,
      setError,
      stopAudio,
      stopText,
    ],
  );

  const handleAudio = useCallback(
    (message: TtsStreamMessage) => {
      if (message.generation_id !== activeGeneration.current) return;
      getPlaybackCoordinator()?.consumePcm(message);
    },
    [getPlaybackCoordinator],
  );

  useEffect(() => {
    let disposed = false;
    void bootstrapChatSession()
      .then((result) => {
        if (disposed) return;
        setHealth(result.health);
        setCharacter(result.character);
        setSessionId(result.sessionId);
        setEventCursor(result.eventCursor);
        setMemories(result.memories);
        setTtsProviders(result.ttsProviders);
        setTtsProviderId(result.ttsProviderId);
        dispatch({ type: "bootstrap", messages: result.messages });
      })
      .catch((runtimeError: unknown) => {
        if (disposed) return;
        setConnection("offline");
        setError(message(runtimeError, "Runtime 不可用"));
      });
    return () => {
      disposed = true;
    };
  }, [setError]);

  useEffect(() => {
    if (!sessionId || eventCursor === null) return;
    const client = new RuntimeSocketClient(
      {
        onConnection: setConnection,
        onEvent: handleEvent,
        onAudio: handleAudio,
        onProtocolError: setError,
      },
      playbackEnabled,
    );
    socketClient.current = client;
    client.start(sessionId, eventCursor);
    return () => {
      client.stop();
      if (socketClient.current === client) socketClient.current = null;
    };
  }, [
    eventCursor,
    handleAudio,
    handleEvent,
    playbackEnabled,
    sessionId,
    setError,
  ]);

  useEffect(
    () => () => {
      playbackCoordinator.current?.dispose();
      playbackCoordinator.current = null;
      textProjector.current?.dispose();
      textProjector.current = null;
    },
    [],
  );

  const onTtsSelected = useCallback((providerId: string) => {
    setTtsProviderId(providerId);
    setTtsProviders((current) =>
      current.map((provider) => ({
        ...provider,
        selected: provider.provider_id === providerId,
      })),
    );
    setHealth((current) =>
      current
        ? {
            ...current,
            providers: { ...current.providers, tts: providerId },
          }
        : current,
    );
  }, []);

  const onReset = useCallback(() => {
    const previousGeneration = activeGeneration.current;
    if (previousGeneration) {
      stopText(previousGeneration);
      stopAudio(previousGeneration);
    }
    activeGeneration.current = null;
    dispatch({ type: "reset" });
    setMemories([]);
    playbackCoordinator.current?.resetSubtitles();
    setSubtitlePlayback(null);
    resetAvatar();
  }, [resetAvatar, stopAudio, stopText]);

  const {
    send,
    interruptActive,
    changeTtsProvider,
    refreshTtsProviders,
    resetAll,
    refreshMemories,
  } = useChatSessionCommands({
    sessionId,
    connection,
    activeGenerationRef: activeGeneration,
    resetting,
    ttsSwitching,
    ttsProviderId,
    primePlayback: () => getPlaybackCoordinator()?.prime(),
    stopText,
    stopAudio,
    resetSubtitles: () => playbackCoordinator.current?.resetSubtitles(),
    setError,
    setResetting,
    setTtsSwitching,
    onTtsProviders: setTtsProviders,
    onTtsSelected,
    onMemories: setMemories,
    onReset,
  });

  const touch = useCallback(() => {
    getPlaybackCoordinator()?.prime();
    avatar.touch();
    if (sessionId) {
      void sendCharacterInteraction(sessionId, "avatar_touch").catch(
        (interactionError: unknown) => {
          setError(message(interactionError, "角色互动同步失败"));
        },
      );
    }
  }, [avatar, getPlaybackCoordinator, sessionId, setError]);

  return {
    ...avatar,
    touch,
    health,
    character,
    sessionId,
    messages: view.messages,
    memories,
    connection,
    error: view.error,
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
    voiceActivity: view.voiceActivity,
    voiceTranscript: view.voiceTranscript,
    subtitlePlayback,
    setVoiceDeviceId: voice.setDeviceId,
    setVoiceActivationMode: voice.setActivationMode,
    beginPushToTalk: voice.beginPushToTalk,
    endPushToTalk: voice.endPushToTalk,
    refreshVoiceDevices: voice.refreshDevices,
    toggleVoice: voice.toggle,
    changeTtsProvider,
    refreshTtsProviders,
    send,
    interruptActive,
    resetAll,
    refreshMemories,
  };
}

function payloadText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
