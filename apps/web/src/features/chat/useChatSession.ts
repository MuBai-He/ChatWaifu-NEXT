import { parseEventEnvelope } from "@chatwaifu/protocol";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  RUNTIME_URL,
  RUNTIME_WS_URL,
  createSession,
  getCharacters,
  getHealth,
  getMemory,
  getMessages,
  getSession,
  interrupt,
  resetSession,
  runStatusSkill,
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
} from "./types";
import { GenerationAudioPlayer } from "./audioPlayer";
import { useChatAvatar } from "./useChatAvatar";
import { useVoiceInput } from "./useVoiceInput";

const SESSION_KEY = "chatwaifu.next.session_id";

export function useChatSession() {
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
  const [skillSummary, setSkillSummary] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [voiceActivity, setVoiceActivity] = useState<
    "idle" | "listening" | "transcribing" | "thinking"
  >("idle");
  const [voiceTranscript, setVoiceTranscript] = useState<string | null>(null);
  const activeGeneration = useRef<string | null>(null);
  const voiceConnected = useRef(false);
  const audioPlayer = useRef<GenerationAudioPlayer | null>(null);

  const onVoiceConnectionChange = useCallback((connected: boolean) => {
    voiceConnected.current = connected;
    if (!connected) setVoiceActivity("idle");
  }, []);
  const voice = useVoiceInput({
    sessionId,
    onError: setError,
    onConnectionChange: onVoiceConnectionChange,
  });

  const getAudioPlayer = useCallback(() => {
    if (!audioPlayer.current && typeof Audio !== "undefined") {
      audioPlayer.current = new GenerationAudioPlayer((url) => new Audio(url), {
        isGenerationActive: (generationId) =>
          generationId === activeGeneration.current,
        onPlaybackStart: startLipSync,
        onPlaybackStop: stopLipSync,
        onPlaybackError: setError,
      });
    }
    return audioPlayer.current;
  }, [startLipSync, stopLipSync]);

  const stopAudio = useCallback(
    (generationId?: string) => {
      audioPlayer.current?.stop();
      stopLipSync();
      if (generationId) invalidateGeneration(generationId);
    },
    [invalidateGeneration, stopLipSync],
  );

  const handleEvent = useCallback(
    (event: RuntimeEvent) => {
      const generationId = event.generation_id ?? undefined;
      switch (event.event_type) {
        case "user.speech_started": {
          const previousGeneration = activeGeneration.current;
          if (previousGeneration) stopAudio(previousGeneration);
          setVoiceActivity("listening");
          setVoiceTranscript(null);
          break;
        }
        case "user.speech_stopped": {
          setVoiceActivity("transcribing");
          break;
        }
        case "user.transcript_partial": {
          setVoiceTranscript(payloadText(event.payload.text));
          break;
        }
        case "user.transcript_final": {
          setVoiceTranscript(payloadText(event.payload.text));
          setVoiceActivity("thinking");
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
          activeGeneration.current = generationId;
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
          setMessages((current) =>
            current.map((message) =>
              message.generationId === generationId
                ? {
                    ...message,
                    text: message.text + payloadText(event.payload.text),
                  }
                : message,
            ),
          );
          break;
        }
        case "assistant.audio_chunk_queued": {
          if (!generationId || generationId !== activeGeneration.current) break;
          const payload = event.payload as unknown as AudioPayload;
          if (voiceConnected.current) {
            startLipSync();
            break;
          }
          getAudioPlayer()?.enqueue({
            generationId,
            url: `${RUNTIME_URL}${payload.url}`,
          });
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
          setMessages((current) =>
            current.map((message) =>
              message.generationId === generationId
                ? { ...message, pending: false }
                : message,
            ),
          );
          void getMemory().then(setMemories);
          setVoiceActivity("idle");
          setVoiceTranscript(null);
          if (voiceConnected.current) {
            window.setTimeout(stopLipSync, 450);
          }
          break;
        }
        case "assistant.generation_cancelled":
        case "conversation.interrupted": {
          if (generationId) stopAudio(generationId);
          setMessages((current) =>
            current.filter(
              (message) =>
                message.generationId !== generationId ||
                message.text.length > 0,
            ),
          );
          activeGeneration.current = null;
          setVoiceActivity("idle");
          break;
        }
        case "system.error_raised": {
          const nested = event.payload.error as
            { message?: string } | undefined;
          setError(nested?.message ?? "Runtime 生成失败。");
          if (nested?.message?.includes("语音转写")) setVoiceActivity("idle");
          break;
        }
      }
    },
    [applyCue, getAudioPlayer, startLipSync, stopAudio, stopLipSync],
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
          setError("收到无法识别的 Runtime 事件，已安全忽略。");
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
        const [history, savedMemories] = await Promise.all([
          getMessages(session.session_id),
          getMemory(),
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
      if (previousGeneration) stopAudio(previousGeneration);
      try {
        await submitText(sessionId, text.trim());
      } catch (sendError: unknown) {
        setError(
          sendError instanceof Error ? sendError.message : "消息发送失败",
        );
      }
    },
    [connection, getAudioPlayer, sessionId, stopAudio],
  );

  const interruptActive = useCallback(async () => {
    if (!sessionId) return;
    const generationId = activeGeneration.current;
    if (generationId) stopAudio(generationId);
    activeGeneration.current = null;
    await interrupt(sessionId).catch((interruptError: unknown) => {
      setError(
        interruptError instanceof Error ? interruptError.message : "打断失败",
      );
    });
  }, [sessionId, stopAudio]);

  const checkStatus = useCallback(async () => {
    if (!sessionId) return;
    try {
      setSkillSummary(await runStatusSkill(sessionId));
    } catch (skillError: unknown) {
      setError(
        skillError instanceof Error ? skillError.message : "Skill 执行失败",
      );
    }
  }, [sessionId]);

  const resetAll = useCallback(async (): Promise<boolean> => {
    if (!sessionId || resetting) return false;
    setResetting(true);
    setError(null);
    const generationId = activeGeneration.current;
    stopAudio(generationId ?? undefined);
    activeGeneration.current = null;
    try {
      await resetSession(sessionId);
      setMessages([]);
      setMemories([]);
      setSkillSummary(null);
      resetAvatar();
      return true;
    } catch (resetError: unknown) {
      setError(resetError instanceof Error ? resetError.message : "重置失败");
      return false;
    } finally {
      setResetting(false);
    }
  }, [resetAvatar, resetting, sessionId, stopAudio]);

  return {
    ...avatar,
    health,
    character,
    sessionId,
    messages,
    memories,
    connection,
    error,
    skillSummary,
    resetting,
    voiceState: voice.state,
    voiceConnected: voice.connected,
    voiceDevices: voice.devices,
    voiceDeviceId: voice.deviceId,
    voiceInputLevel: voice.inputLevel,
    voiceActivationMode: voice.activationMode,
    voiceTransmitting: voice.transmitting,
    voiceActivity,
    voiceTranscript,
    setVoiceDeviceId: voice.setDeviceId,
    setVoiceActivationMode: voice.setActivationMode,
    beginPushToTalk: voice.beginPushToTalk,
    endPushToTalk: voice.endPushToTalk,
    refreshVoiceDevices: voice.refreshDevices,
    toggleVoice: voice.toggle,
    send,
    interruptActive,
    checkStatus,
    resetAll,
  };
}

function payloadText(value: unknown): string {
  return typeof value === "string" ? value : "";
}
