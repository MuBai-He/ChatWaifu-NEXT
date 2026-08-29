import type { DomainEvent } from "@chatwaifu/protocol";

import type { ChatMessage } from "./types";

export type VoiceActivity = "idle" | "listening" | "transcribing" | "thinking";

export interface RuntimeViewState {
  messages: ChatMessage[];
  voiceActivity: VoiceActivity;
  voiceTranscript: string | null;
  error: string | null;
}

export const initialRuntimeViewState: RuntimeViewState = {
  messages: [],
  voiceActivity: "idle",
  voiceTranscript: null,
  error: null,
};

export type RuntimeViewAction =
  | { type: "bootstrap"; messages: ChatMessage[] }
  | { type: "runtime_event"; event: DomainEvent }
  | { type: "text_revealed"; generationId: string; text: string }
  | { type: "text_completed"; generationId: string }
  | {
      type: "voice_status";
      activity: VoiceActivity;
      transcript?: string | null;
    }
  | { type: "set_error"; error: string | null }
  | { type: "reset" };

export function runtimeEventReducer(
  state: RuntimeViewState,
  action: RuntimeViewAction,
): RuntimeViewState {
  if (action.type === "bootstrap") {
    return { ...state, messages: action.messages };
  }
  if (action.type === "text_revealed") {
    return {
      ...state,
      messages: state.messages.map((message) =>
        message.generationId === action.generationId
          ? { ...message, text: message.text + action.text }
          : message,
      ),
    };
  }
  if (action.type === "text_completed") {
    return {
      ...state,
      messages: state.messages.map((message) =>
        message.generationId === action.generationId
          ? { ...message, pending: false }
          : message,
      ),
    };
  }
  if (action.type === "voice_status") {
    return {
      ...state,
      voiceActivity: action.activity,
      voiceTranscript:
        action.transcript === undefined
          ? state.voiceTranscript
          : action.transcript,
    };
  }
  if (action.type === "set_error") return { ...state, error: action.error };
  if (action.type === "reset")
    return { ...initialRuntimeViewState, messages: [] };

  const { event } = action;
  switch (event.event_type) {
    case "session.data_reset":
      return { ...initialRuntimeViewState, messages: [] };
    case "user.speech_started":
      return {
        ...state,
        voiceActivity: "listening",
        voiceTranscript: null,
      };
    case "user.speech_stopped":
      return { ...state, voiceActivity: "transcribing" };
    case "user.transcript_partial":
      return {
        ...state,
        voiceTranscript: payloadText(event.payload.text),
      };
    case "user.transcript_final":
      return {
        ...state,
        voiceActivity: "thinking",
        voiceTranscript: payloadText(event.payload.text),
      };
    case "voice.utterance_ignored":
      return {
        ...state,
        voiceActivity: "idle",
        voiceTranscript: null,
      };
    case "user.turn_committed":
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            id: event.turn_id ?? event.event_id,
            role: "user",
            text: payloadText(event.payload.text),
          },
        ],
      };
    case "assistant.generation_started":
      if (!event.generation_id) return state;
      return {
        ...state,
        voiceActivity: "thinking",
        messages: [
          ...state.messages,
          {
            id: event.generation_id,
            role: "assistant",
            text: "",
            generationId: event.generation_id,
            pending: true,
          },
        ],
      };
    case "assistant.generation_completed":
      return {
        ...state,
        voiceActivity: "idle",
        voiceTranscript: null,
      };
    case "assistant.generation_cancelled":
    case "conversation.interrupted":
      return {
        ...state,
        messages: state.messages.filter(
          (message) =>
            message.generationId !== event.generation_id ||
            message.text.length > 0,
        ),
        voiceActivity: "idle",
      };
    case "system.error_raised": {
      const nested = event.payload.error;
      const error =
        typeof nested === "object" &&
        nested !== null &&
        "message" in nested &&
        typeof nested.message === "string"
          ? nested.message
          : "Runtime 生成失败。";
      return {
        ...state,
        error,
        voiceActivity: error.includes("语音转写")
          ? "idle"
          : state.voiceActivity,
      };
    }
    default:
      return state;
  }
}

function payloadText(value: unknown): string {
  return typeof value === "string" ? value : "";
}
