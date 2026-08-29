import type { AvatarCue, DomainEvent, MemoryRecord } from "@chatwaifu/protocol";

export type {
  CharacterKernelSnapshot,
  SessionSnapshot,
} from "@chatwaifu/protocol";
export type {
  AliyunCloudTtsConfiguration,
  AliyunCloudTtsProviderId,
  AliyunCosyVoiceTtsConfiguration,
  AliyunTtsConfiguration,
  AudioPayload,
  CharacterProfile,
  CompanionSettings,
  CompanionStatus,
  ModelProviderKind,
  ModelRole,
  ModelRoleConfiguration,
  ResourceStatus,
  RuntimeHealth,
  SessionResetResult,
  TtsProviderSnapshot,
  TtsConfigurationJsonSchema,
  TtsConfigurationRegistration,
  TtsConfigurationSnapshot,
  TtsConfigurationUiField,
  TtsStreamMessage,
} from "./runtime-client/contracts";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  generationId?: string;
  pending?: boolean;
}

// `content` is retained as a compatibility alias for older memory UI fixtures;
// Runtime truth remains the generated protocol's `text` field.
export type MemoryItem = MemoryRecord & { content?: string };

export type RuntimeEvent = DomainEvent;

export interface AvatarCuePayload {
  cue: AvatarCue;
}
