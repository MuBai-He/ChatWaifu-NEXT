import type { AvatarCue, MemoryRecord } from "@chatwaifu/protocol";

export interface RuntimeHealth {
  status: "ok" | "degraded";
  version: string;
  providers: { llm: string; tts: string; stt?: string };
}

export interface TtsProviderSnapshot {
  provider_id: string;
  display_name: string;
  model: string;
  languages: string[];
  supports_voice_cloning: boolean;
  supports_style: boolean;
  supports_speed: boolean;
  supports_pitch: boolean;
  native_streaming: boolean;
  local_only: boolean;
  status: "ready" | "busy" | "starting" | "degraded" | "unavailable";
  model_loaded: boolean;
  queue_depth: number;
  device?: string | null;
  detail?: string | null;
  selected: boolean;
}

export interface CharacterProfile {
  character_id: string;
  display_name: string;
  tagline: string;
  greeting: string;
  accent_color: string;
  voice_profile: {
    voice_id: string;
    display_name: string;
    language: string;
    provider: string;
    model: string;
    speaker_id: number;
    speed: number;
    license: string;
  };
  content_notice: string;
}

export interface SessionSnapshot {
  session_id: string;
  character_id: string;
  state: string;
}

export interface SessionResetResult {
  session_id: string;
  turns_deleted: number;
  events_deleted: number;
  memories_deleted: number;
  audio_assets_deleted: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  generationId?: string;
  pending?: boolean;
}

export type MemoryItem = MemoryRecord & { content: string };

export interface RuntimeEvent {
  event_id: string;
  event_type: string;
  session_id?: string | null;
  turn_id?: string | null;
  generation_id?: string | null;
  sequence?: number | null;
  payload: Record<string, unknown>;
}

export interface AudioPayload {
  url: string;
  text: string;
  duration_ms: number;
  tts_provider: string;
}

export interface AvatarCuePayload {
  cue: AvatarCue;
}
