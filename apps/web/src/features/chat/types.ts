import type { AvatarCue } from "@chatwaifu/protocol";

export interface RuntimeHealth {
  status: "ok" | "degraded";
  version: string;
  providers: { llm: string; tts: string };
}

export interface CharacterProfile {
  character_id: string;
  display_name: string;
  tagline: string;
  greeting: string;
  accent_color: string;
}

export interface SessionSnapshot {
  session_id: string;
  character_id: string;
  state: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  generationId?: string;
  pending?: boolean;
}

export interface MemoryItem {
  memory_id: string;
  content: string;
  state: string;
}

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
