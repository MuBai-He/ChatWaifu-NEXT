import { z } from "zod";

const dateTime = z.string().datetime({ offset: true });

export const runtimeHealthSchema = z
  .object({
    status: z.enum(["ok", "degraded"]),
    version: z.string().min(1),
    providers: z
      .object({
        llm: z.string(),
        tts: z.string(),
        stt: z.string().optional(),
      })
      .passthrough(),
    resources: z
      .object({
        state: z.enum(["active", "sleeping", "stopping"]),
        idle_seconds: z.number().nonnegative(),
        sleep_count: z.number().int().nonnegative(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough();

export type RuntimeHealth = z.infer<typeof runtimeHealthSchema>;

const voiceProfileSchema = z
  .object({
    voice_id: z.string(),
    display_name: z.string(),
    language: z.string(),
    provider: z.string(),
    model: z.string(),
    speaker_id: z.number().int(),
    speed: z.number(),
    license: z.string(),
  })
  .passthrough();

export const characterProfileSchema = z
  .object({
    character_id: z.string().min(1),
    display_name: z.string().min(1),
    tagline: z.string(),
    greeting: z.string(),
    accent_color: z.string(),
    voice_profile: voiceProfileSchema,
    content_notice: z.string(),
  })
  .passthrough();

export type CharacterProfile = z.infer<typeof characterProfileSchema>;

export const sessionMessageSchema = z
  .object({
    turn_id: z.string().uuid(),
    role: z.enum(["user", "assistant"]),
    committed_text: z.string(),
  })
  .passthrough();

export type SessionMessage = z.infer<typeof sessionMessageSchema>;

export const sessionRecoverySchema = z
  .object({
    schema_version: z.literal("1.0"),
    session_id: z.string().uuid(),
    messages: z.array(sessionMessageSchema),
    after_sequence: z.number().int().nonnegative(),
    last_sequence: z.number().int().nonnegative(),
    active_generation_id: z.string().uuid().nullable(),
  })
  .passthrough();

export type SessionRecovery = z.infer<typeof sessionRecoverySchema>;

export const sessionResetResultSchema = z
  .object({
    session_id: z.string().uuid(),
    turns_deleted: z.number().int().nonnegative(),
    events_deleted: z.number().int().nonnegative(),
    memories_deleted: z.number().int().nonnegative(),
    audio_assets_deleted: z.number().int().nonnegative(),
    audio_assets_pending_cleanup: z.number().int().nonnegative(),
    audio_cleanup_complete: z.boolean(),
    scope: z.object({
      character_id: z.string().min(1),
      user_scope: z.string().min(1),
      conversation: z.literal("current_session"),
      audio: z.literal("current_session"),
      memory: z.literal("current_character_user"),
      character_state: z.literal("current_character_user"),
    }),
  })
  .passthrough();

export type SessionResetResult = z.infer<typeof sessionResetResultSchema>;

export const ttsProviderPresentationSchema = z
  .object({
    group_id: z.string().min(1).nullish(),
    group_display_name: z.string().min(1).nullish(),
    variant_label: z.string().min(1).nullish(),
    group_default: z.boolean(),
  })
  .passthrough();

export const ttsProviderSnapshotSchema = z
  .object({
    provider_id: z.string().min(1),
    display_name: z.string().min(1),
    model: z.string(),
    languages: z.array(z.string()),
    supports_voice_cloning: z.boolean(),
    supports_style: z.boolean(),
    supports_speed: z.boolean(),
    supports_pitch: z.boolean(),
    native_streaming: z.boolean(),
    local_only: z.boolean(),
    status: z.enum(["ready", "busy", "starting", "degraded", "unavailable"]),
    model_loaded: z.boolean(),
    queue_depth: z.number().int().nonnegative(),
    device: z.string().nullish(),
    detail: z.string().nullish(),
    selected: z.boolean(),
    presentation: ttsProviderPresentationSchema.nullish(),
  })
  .passthrough();

export type TtsProviderSnapshot = z.infer<typeof ttsProviderSnapshotSchema>;
export type TtsProviderPresentation = z.infer<
  typeof ttsProviderPresentationSchema
>;

const ttsConfigurationFieldSchema = z
  .object({
    title: z.string().optional(),
    description: z.string().optional(),
    type: z.enum(["string", "number", "integer", "boolean"]).optional(),
    enum: z.array(z.union([z.string(), z.number(), z.boolean()])).optional(),
    default: z.unknown().optional(),
    minimum: z.number().optional(),
    maximum: z.number().optional(),
  })
  .passthrough();

export const ttsConfigurationJsonSchema = z
  .object({
    title: z.string().optional(),
    description: z.string().optional(),
    properties: z.record(z.string(), ttsConfigurationFieldSchema).default({}),
    required: z.array(z.string()).default([]),
  })
  .passthrough();

export const ttsConfigurationUiFieldSchema = z
  .object({
    key: z.string().min(1),
    label: z.string().min(1),
    control: z.enum([
      "toggle",
      "text",
      "secret",
      "select",
      "number",
      "textarea",
    ]),
    advanced: z.boolean().default(false),
    options: z
      .array(
        z.object({
          value: z.union([z.string(), z.number(), z.boolean()]),
          label: z.string(),
        }),
      )
      .default([]),
    minimum: z.number().nullish(),
    maximum: z.number().nullish(),
    step: z.number().nullish(),
    placeholder: z.string().default(""),
    help_text: z.string().default(""),
  })
  .passthrough();

export const ttsConfigurationUiSchema = z
  .object({
    schema_version: z.string().min(1),
    fields: z.array(ttsConfigurationUiFieldSchema),
  })
  .passthrough();

export const ttsConfigurationSnapshotSchema = z
  .object({
    provider_id: z.string().min(1),
    api_key_configured: z.boolean().optional(),
    updated_at: dateTime.optional(),
  })
  .passthrough();

export const ttsConfigurationCredentialSchema = z
  .object({
    kind: z.literal("api_key"),
    field_key: z.string().min(1),
    configured_field: z.string().min(1),
    clear_field: z.string().min(1),
    fallback_provider_id: z.string().min(1).nullish(),
  })
  .passthrough();

export const ttsConfigurationRegistrationSchema = z
  .object({
    provider_id: z.string().min(1),
    display_name: z.string().min(1),
    configuration_schema_version: z.string().min(1),
    configuration_schema: ttsConfigurationJsonSchema,
    ui_schema: ttsConfigurationUiSchema,
    credential: ttsConfigurationCredentialSchema.nullable(),
    presentation: ttsProviderPresentationSchema,
    configuration: ttsConfigurationSnapshotSchema,
  })
  .passthrough();

export type TtsConfigurationJsonSchema = z.infer<
  typeof ttsConfigurationJsonSchema
>;
export type TtsConfigurationSnapshot = z.infer<
  typeof ttsConfigurationSnapshotSchema
>;
export type TtsConfigurationRegistration = z.infer<
  typeof ttsConfigurationRegistrationSchema
>;
export type TtsConfigurationUiField = z.infer<
  typeof ttsConfigurationUiFieldSchema
>;
export type TtsConfigurationCredential = z.infer<
  typeof ttsConfigurationCredentialSchema
>;

export const modelRoleSchema = z.enum([
  "chat",
  "memory_extraction",
  "memory_summary",
  "embedding",
]);
export const modelProviderKindSchema = z.enum([
  "demo",
  "openai_compatible",
  "local_hash",
  "disabled",
]);

export const modelRoleConfigurationSchema = z
  .object({
    role: modelRoleSchema,
    provider: modelProviderKindSchema,
    model: z.string(),
    base_url: z.string(),
    timeout_seconds: z.number().positive(),
    context_window: z.number().int().positive(),
    enabled: z.boolean(),
    api_key_configured: z.boolean(),
    updated_at: dateTime,
  })
  .passthrough();

export type ModelRole = z.infer<typeof modelRoleSchema>;
export type ModelProviderKind = z.infer<typeof modelProviderKindSchema>;
export type ModelRoleConfiguration = z.infer<
  typeof modelRoleConfigurationSchema
>;

export const resourceStatusSchema = z
  .object({
    state: z.enum(["active", "sleeping", "stopping"]),
    idle_seconds: z.number().nonnegative(),
    sleep_count: z.number().int().nonnegative(),
    last_sleep_at: dateTime.nullish(),
    last_wake_at: dateTime.nullish(),
  })
  .passthrough();

export const companionSettingsSchema = z
  .object({
    schema_version: z.literal("1.0"),
    wake_phrase_enabled: z.boolean(),
    wake_phrases: z.array(z.string()),
    quiet_hours_enabled: z.boolean(),
    quiet_start: z.string(),
    quiet_end: z.string(),
    proactive_enabled: z.boolean(),
    proactive_idle_minutes: z.number().int().nonnegative(),
    proactive_cooldown_minutes: z.number().int().nonnegative(),
    proactive_daily_budget: z.number().int().nonnegative(),
    resource_sleep_enabled: z.boolean(),
    resource_idle_minutes: z.number().int().nonnegative(),
    updated_at: dateTime,
  })
  .passthrough();

export const companionStatusSchema = z
  .object({
    schema_version: z.literal("1.0"),
    settings: companionSettingsSchema,
    resources: resourceStatusSchema,
    proactive_today: z.number().int().nonnegative(),
    last_proactive_at: dateTime.nullish(),
  })
  .passthrough();

export type ResourceStatus = z.infer<typeof resourceStatusSchema>;
export type CompanionSettings = z.infer<typeof companionSettingsSchema>;
export type CompanionStatus = z.infer<typeof companionStatusSchema>;

export const ttsStreamMessageSchema = z
  .object({
    type: z.literal("chatwaifu.tts_stream"),
    schema_version: z.literal("1.0"),
    phase: z.enum(["started", "chunk", "completed", "cancelled"]),
    session_id: z.string().uuid(),
    turn_id: z.string().uuid(),
    generation_id: z.string().uuid(),
    stream_id: z.string().uuid(),
    segment_id: z.string().uuid(),
    segment_index: z.number().int().nonnegative(),
    text: z.string(),
    sequence: z.number().int().nonnegative(),
    sample_rate: z.number().int().positive(),
    channels: z.number().int().positive(),
    native_streaming: z.boolean(),
    pcm16_base64: z.string(),
    duration_ms: z.number().int().nonnegative(),
    provider_id: z.string(),
    model: z.string(),
    reason: z.string().nullish(),
  })
  .passthrough();

export type TtsStreamMessage = z.infer<typeof ttsStreamMessageSchema>;

export const audioPayloadSchema = z
  .object({
    url: z.string(),
    text: z.string(),
    duration_ms: z.number().int().nonnegative(),
    stream_id: z.string().uuid(),
    segment_id: z.string().uuid(),
    segment_index: z.number().int().nonnegative(),
    tts_provider: z.string(),
    streamed_live: z.boolean().optional(),
  })
  .passthrough();

export type AudioPayload = z.infer<typeof audioPayloadSchema>;
