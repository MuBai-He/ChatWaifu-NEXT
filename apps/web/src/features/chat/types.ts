import type { AvatarCue, MemoryRecord } from "@chatwaifu/protocol";

export interface RuntimeHealth {
  status: "ok" | "degraded";
  version: string;
  providers: { llm: string; tts: string; stt?: string };
  resources?: {
    state: "active" | "sleeping" | "stopping";
    idle_seconds: number;
    sleep_count: number;
  };
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

interface AliyunCloudTtsConfigurationBase {
  enabled: boolean;
  model: string;
  voice_id: string;
  region: "beijing" | "singapore";
  workspace_id: string;
  sample_rate: 8000 | 16000 | 24000 | 48000;
  speech_rate: number;
  volume: number;
  pitch_rate: number;
  instruction: string;
  timeout_seconds: number;
  max_audio_bytes: number;
  api_key_configured: boolean;
  updated_at: string;
}

export interface AliyunTtsConfiguration extends AliyunCloudTtsConfigurationBase {
  provider_id: "aliyun_qwen_realtime";
  language_type:
    | "Auto"
    | "Chinese"
    | "English"
    | "German"
    | "Italian"
    | "Portuguese"
    | "Spanish"
    | "Japanese"
    | "Korean"
    | "French"
    | "Russian";
}

export interface AliyunCosyVoiceTtsConfiguration extends AliyunCloudTtsConfigurationBase {
  provider_id: "aliyun_cosyvoice_realtime";
  language_type:
    | "auto"
    | "zh"
    | "en"
    | "fr"
    | "de"
    | "ja"
    | "ko"
    | "ru"
    | "pt"
    | "th"
    | "id"
    | "vi"
    | "es"
    | "it"
    | "ms"
    | "fil"
    | "ar";
}

export type AliyunCloudTtsConfiguration =
  AliyunTtsConfiguration | AliyunCosyVoiceTtsConfiguration;

export type AliyunCloudTtsProviderId =
  AliyunCloudTtsConfiguration["provider_id"];

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
  stream_id: string;
  segment_id: string;
  segment_index: number;
  tts_provider: string;
  streamed_live?: boolean;
}

export interface TtsStreamMessage {
  type: "chatwaifu.tts_stream";
  schema_version: "1.0";
  phase: "started" | "chunk" | "completed" | "cancelled";
  session_id: string;
  turn_id: string;
  generation_id: string;
  stream_id: string;
  segment_id: string;
  segment_index: number;
  text: string;
  sequence: number;
  sample_rate: number;
  channels: number;
  native_streaming: boolean;
  pcm16_base64: string;
  duration_ms: number;
  provider_id: string;
  model: string;
  reason?: string | null;
}

export interface AvatarCuePayload {
  cue: AvatarCue;
}

export type ModelRole =
  "chat" | "memory_extraction" | "memory_summary" | "embedding";

export type ModelProviderKind =
  "demo" | "openai_compatible" | "local_hash" | "disabled";

export interface ModelRoleConfiguration {
  role: ModelRole;
  provider: ModelProviderKind;
  model: string;
  base_url: string;
  timeout_seconds: number;
  context_window: number;
  enabled: boolean;
  api_key_configured: boolean;
  updated_at: string;
}

export interface CharacterKernelSnapshot {
  character_id: string;
  user_scope: string;
  revision: number;
  affect: {
    valence: number;
    arousal: number;
    energy: number;
    attention: number;
    embarrassment: number;
    tension: number;
    updated_at: string;
  };
  relationship: {
    familiarity: number;
    trust: number;
    affinity: number;
    comfort: number;
    recent_tension: number;
    interaction_count: number;
    stage: "acquaintance" | "familiar" | "trusted" | "close";
    preferred_address: string | null;
    updated_at: string;
  };
}

export interface CompanionSettings {
  schema_version: "1.0";
  wake_phrase_enabled: boolean;
  wake_phrases: string[];
  quiet_hours_enabled: boolean;
  quiet_start: string;
  quiet_end: string;
  proactive_enabled: boolean;
  proactive_idle_minutes: number;
  proactive_cooldown_minutes: number;
  proactive_daily_budget: number;
  resource_sleep_enabled: boolean;
  resource_idle_minutes: number;
  updated_at: string;
}

export interface ResourceStatus {
  state: "active" | "sleeping" | "stopping";
  idle_seconds: number;
  sleep_count: number;
  last_sleep_at?: string | null;
  last_wake_at?: string | null;
}

export interface CompanionStatus {
  schema_version: "1.0";
  settings: CompanionSettings;
  resources: ResourceStatus;
  proactive_today: number;
  last_proactive_at?: string | null;
}
