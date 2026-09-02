// Generated from chatwaifu-protocol Pydantic models. Run make generate-protocol; do not edit.

export type ByteLength = number
export type Channels = number
export type Codec = 'pcm_s16le' | 'opus'
export type DurationMs = number
export type EndOfStream = boolean
export type GenerationId = string | null
export type PtsMs = number
export type SampleRate = number
export type Sequence = number
export type StreamId = string
export type AvatarId = string
export type Expressions = string[]
export type GazeTargets = string[]
export type HitAreas = string[]
export type Motions = string[]
export type RendererKind = string
export type States = string[]
export type SupportsLipsync = boolean
export type CueId = string
export type DurationMs1 = number | null
export type GenerationId1 = string | null
export type Intensity = number
export type Interruptible = boolean
export type Kind = 'state' | 'expression' | 'motion' | 'gaze' | 'speech' | 'override'
export type JsonValue =
  | string
  | number
  | boolean
  | JsonValue[]
  | {
      [k: string]: JsonValue
    }
  | null
export type Name = string
export type Priority = number
export type StartAnchor = string
export type AvatarId1 = string
export type InteractionId = string
export type Kind1 = 'pointer' | 'touch' | 'gaze' | 'drag' | 'system'
export type Target = string | null
export type X = number | null
export type Y = number | null
export type AuthSessionId = string
/**
 * Interactive authorization methods implemented by the local adapter; an empty list means connection provisioning is external
 *
 * @maxItems 8
 */
export type AuthorizationMethods =
  | []
  | [ChannelAuthorizationMethod]
  | [ChannelAuthorizationMethod, ChannelAuthorizationMethod]
  | [ChannelAuthorizationMethod, ChannelAuthorizationMethod, ChannelAuthorizationMethod]
  | [ChannelAuthorizationMethod, ChannelAuthorizationMethod, ChannelAuthorizationMethod, ChannelAuthorizationMethod]
  | [
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod
    ]
  | [
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod
    ]
  | [
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod
    ]
  | [
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod,
      ChannelAuthorizationMethod
    ]
/**
 * Interactive authorization methods exposed to trusted local clients.
 */
export type ChannelAuthorizationMethod = 'qr_code'
/**
 * @minItems 1
 */
export type ChatTypes = [ChannelChatType, ...ChannelChatType[]]
/**
 * Conversation shapes understood by this schema major.
 */
export type ChannelChatType = 'direct' | 'group'
/**
 * @minItems 1
 */
export type InboundMessageKinds = [ChannelMessageKind, ...ChannelMessageKind[]]
/**
 * Message payload kinds understood by this schema major.
 */
export type ChannelMessageKind = 'text'
export type MaxTextChars = number
/**
 * @minItems 1
 */
export type OutboundMessageKinds = [ChannelMessageKind, ...ChannelMessageKind[]]
export type SupportsCancellation = boolean
export type SupportsDeliveryAck = boolean
export type SupportsPartialReplies = boolean
export type SupportsProactiveMessages = boolean
export type SupportsTyping = boolean
/**
 * Opaque provider-scoped account identity; never a credential
 */
export type AccountKey = string | null
/**
 * Opaque provider-scoped sender identities admitted by this connection; an empty list admits no senders
 *
 * @maxItems 64
 */
export type AllowedSenderKeys = string[]
export type CharacterId = string
export type ConnectionId = string
export type Enabled = boolean
export type Name1 = string
/**
 * Runtime-owned stable isolation key for persona relationship and memory state; it is not a provider identity and must match the configured connection
 */
export type PrincipalScope = string
export type ProviderId = string
export type SchemaVersion = '1.0'
/**
 * Recent authenticated adapter activity window used by local health presentation; it is not a generation or provider-delivery deadline
 */
export type TimeoutSeconds = number
export type CreatedAt = string
export type Code = string
export type Component = string
export type CorrelationId = string | null
export type Message = string
export type Retryable = boolean
export type LastSeenAt = string | null
export type Revision = number
export type SchemaVersion1 = '1.0'
export type ChannelConnectionStatus = 'untested' | 'ready' | 'degraded' | 'error' | 'disabled'
export type UpdatedAt = string
export type CreatedAt1 = string
export type ExpiresAt = string
/**
 * Interactive authorization methods exposed to trusted local clients.
 */
export type ChannelAuthorizationMethod1 = 'qr_code'
export type PollAfterMs = number | null
export type ProviderId1 = string
/**
 * Opaque content rendered as a QR code by the trusted local settings client
 */
export type QrCodeContent = string | null
export type SchemaVersion2 = '1.0'
/**
 * Provider-neutral state for one short-lived authorization session.
 */
export type ChannelAuthorizationStatus =
  'pending' | 'scanned' | 'verification_required' | 'confirmed' | 'expired' | 'cancelled' | 'failed'
export type StatusMessage = string | null
export type UpdatedAt1 = string
export type VerificationRequired = boolean
export type CharacterId1 = string
export type ConnectionName = string | null
/**
 * Interactive authorization methods exposed to trusted local clients.
 */
export type ChannelAuthorizationMethod2 = 'qr_code'
/**
 * Runtime-owned relationship and memory scope, never a provider identity
 */
export type PrincipalScope1 = string
export type ProviderId2 = string
export type SchemaVersion3 = '1.0'
export type SchemaVersion4 = '1.0'
export type VerificationCode = string
export type Attempt = number
export type ChannelTurnId = string
export type ConnectionId1 = string
export type CreatedAt2 = string
export type DeliveredAt = string | null
export type DeliveryId = string
export type LeaseExpiresAt = string | null
export type LeaseId = string | null
export type ProviderMessageId = string | null
export type SchemaVersion5 = '1.0'
export type ChannelDeliveryStatus = 'pending' | 'sending' | 'delivered' | 'failed' | 'cancelled'
export type UpdatedAt2 = string
export type AcknowledgedAt = string
export type ChannelTurnId1 = string
export type DeliveryId1 = string
export type LeaseId1 = string
export type ProviderMessageId1 = string | null
export type SchemaVersion6 = '1.0'
export type Status = 'delivered' | 'failed' | 'cancelled'
export type ChannelTurnId2 = string
export type DeliveryId2 = string
export type LeaseId2 = string
export type LeaseSeconds = number
export type SchemaVersion7 = '1.0'
export type ChannelTurnId3 = string | null
export type ExternalMessageId = string | null
export type RetryAfterMs = number | null
export type SchemaVersion8 = '1.0'
export type CheckedAt = string
export type EnabledConnectionCount = number
export type ProviderCount = number
export type SchemaVersion9 = '1.0'
export type ChannelGatewayStatus = 'ready' | 'degraded' | 'error'
/**
 * Opaque provider-scoped account identity copied from the connection; the gateway must reject a mismatch
 */
export type AccountKey1 = string | null
/**
 * Conversation shapes understood by this schema major.
 */
export type ChannelChatType1 = 'direct' | 'group'
export type ConnectionId2 = string
/**
 * Opaque provider-scoped direct-conversation identity
 */
export type ConversationKey = string
/**
 * Untrusted display label; never identity, authorization, or instruction
 */
export type ConversationLabel = string | null
/**
 * Opaque provider-scoped inbound message identity used for idempotency; stable across retries within the connection
 */
export type ExternalMessageId1 = string
export type Kind2 = 'text'
/**
 * Runtime-owned privacy and memory isolation key copied from the connection; the gateway must reject a mismatch
 */
export type PrincipalScope2 = string
export type ReceivedAt = string
export type ReplyToExternalMessageId = string | null
export type SchemaVersion10 = '1.0'
/**
 * Untrusted display label; never identity, authorization, or instruction
 */
export type SenderDisplayName = string | null
/**
 * Opaque provider-scoped sender identity; never a display name
 */
export type SenderKey = string
export type Text = string
export type Description = string
export type Name2 = string
export type ProviderId3 = string
export type SchemaVersion11 = '1.0'
export type Version = string
export type AccountKey2 = string | null
export type ChannelTurnId4 = string
/**
 * Conversation shapes understood by this schema major.
 */
export type ChannelChatType2 = 'direct' | 'group'
export type CompletedAt = string | null
export type ConnectionId3 = string
export type ConversationKey1 = string
/**
 * Untrusted display label only
 */
export type ConversationLabel1 = string | null
export type CreatedAt3 = string
export type DeliveryId3 = string | null
export type ExternalMessageId2 = string
export type GenerationId2 = string
export type PrincipalScope3 = string
export type ReplyText = string | null
export type Revision1 = number
export type SchemaVersion12 = '1.0'
/**
 * Untrusted display label only
 */
export type SenderDisplayName1 = string | null
export type SenderKey1 = string
export type SessionId = string
export type ChannelTurnStatus =
  'accepted' | 'processing' | 'completed' | 'cancelling' | 'cancelled' | 'failed' | 'timed_out'
export type TurnId = string
export type UpdatedAt3 = string
export type Accepted = boolean
export type AcknowledgedAt1 = string
export type ChannelTurnId5 = string
export type Revision2 = number
export type SchemaVersion13 = '1.0'
export type Reason = string
export type RequestedAt = string
export type SchemaVersion14 = '1.0'
export type AcceptedAt = string
export type AccountKey3 = string | null
export type ChannelTurnId6 = string
/**
 * Conversation shapes understood by this schema major.
 */
export type ChannelChatType3 = 'direct' | 'group'
export type ConnectionId4 = string
export type ConversationKey2 = string
/**
 * Untrusted display label only
 */
export type ConversationLabel2 = string | null
export type Duplicate = boolean
export type ExternalMessageId3 = string
export type GenerationId3 = string
export type PollAfterMs1 = number | null
export type PrincipalScope4 = string
export type Revision3 = number
export type SchemaVersion15 = '1.0'
/**
 * Untrusted display label only
 */
export type SenderDisplayName2 = string | null
export type SenderKey2 = string
export type SessionId1 = string
export type TurnId1 = string
export type Arousal = number
export type Attention = number
export type Embarrassment = number
export type Energy = number
export type Tension = number
export type UpdatedAt4 = string
export type Valence = number
export type CharacterId2 = string
export type Affinity = number
export type Comfort = number
export type Familiarity = number
export type InteractionCount = number
export type PreferredAddress = string | null
export type RecentTension = number
export type Stage = 'acquaintance' | 'familiar' | 'trusted' | 'close'
export type Trust = number
export type UpdatedAt5 = string
export type Revision4 = number
export type UserScope = string
export type Command = SessionStartCommand | TextSendCommand | ConversationInterruptCommand | PlaybackAckCommand
export type CommandId = string
export type CommandType = 'cmd.session.start'
export type CorrelationId1 = string | null
export type ExpectedRevision = number | null
export type GenerationId4 = string | null
export type IssuedAt = string
export type Issuer = string
export type CharacterId3 = string
export type SchemaVersion16 = string
export type SessionId2 = string | null
export type TurnId2 = string | null
export type CommandId1 = string
export type CommandType1 = 'cmd.text.send'
export type CorrelationId2 = string | null
export type ExpectedRevision1 = number | null
export type GenerationId5 = string | null
export type IssuedAt1 = string
export type Issuer1 = string
export type Text1 = string
export type SchemaVersion17 = string
export type SessionId3 = string | null
export type TurnId3 = string | null
export type CommandId2 = string
export type CommandType2 = 'cmd.conversation.interrupt'
export type CorrelationId3 = string | null
export type ExpectedRevision2 = number | null
export type GenerationId6 = string | null
export type IssuedAt2 = string
export type Issuer2 = string
export type Reason1 = string
export type SchemaVersion18 = string
export type SessionId4 = string | null
export type TurnId4 = string | null
export type CommandId3 = string
export type CommandType3 = 'cmd.playback.ack'
export type CorrelationId4 = string | null
export type ExpectedRevision3 = number | null
export type GenerationId7 = string | null
export type IssuedAt3 = string
export type Issuer3 = string
export type BufferedMs = number
export type ClientClockMs = number
export type Phase = 'started' | 'progress' | 'stopped' | 'queue_cleared'
export type PlayedPtsMs = number
export type Reason2 = ('ended' | 'interrupted' | 'error' | 'queue_cleared') | null
export type SegmentId = string
export type StreamId1 = string
export type Transport = 'audio_element' | 'webrtc'
export type SchemaVersion19 = string
export type SessionId5 = string | null
export type TurnId5 = string | null
export type GenerationId8 = string
export type InterruptionInitiator = 'user' | 'system' | 'skill'
export type InterruptionId = string
export type Reason3 = string
export type RequestedAt1 = string
export type SessionId6 = string
export type EventModel =
  | SessionCreatedEvent
  | UserTurnCommittedEvent
  | UserSpeechStartedEvent
  | UserSpeechStoppedEvent
  | UserTranscriptPartialEvent
  | UserTranscriptFinalEvent
  | AssistantGenerationStartedEvent
  | AssistantPlaybackStartedEvent
  | AssistantPlaybackProgressEvent
  | AssistantPlaybackStoppedEvent
  | AssistantSpokenTextCommittedEvent
  | AvatarCueEmittedEvent
  | ErrorRaisedEvent
  | GenericCoreEvent
export type CausationId = string | null
export type CorrelationId5 = string | null
export type EventId = string
export type EventType = 'session.created'
export type GenerationId9 = string | null
export type OccurredAt = string
export type CharacterId4 = string
export type PrivacyLevel = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion20 = string
export type Sequence1 = number | null
export type SessionId7 = string | null
export type SkillRunId = string | null
export type Source = string
export type TurnId6 = string | null
export type CausationId1 = string | null
export type CorrelationId6 = string | null
export type EventId1 = string
export type EventType1 = 'user.turn_committed'
export type GenerationId10 = string | null
export type OccurredAt1 = string
export type Text2 = string
export type PrivacyLevel1 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion21 = string
export type Sequence2 = number | null
export type SessionId8 = string | null
export type SkillRunId1 = string | null
export type Source1 = string
export type TurnId7 = string | null
export type CausationId2 = string | null
export type CorrelationId7 = string | null
export type EventId2 = string
export type EventType2 = 'user.speech_started'
export type GenerationId11 = string | null
export type OccurredAt2 = string
export type AudioStreamId = string
export type Channels1 = number
export type SampleRate1 = number
export type UtteranceId = string
export type PrivacyLevel2 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion22 = string
export type Sequence3 = number | null
export type SessionId9 = string | null
export type SkillRunId2 = string | null
export type Source2 = string
export type TurnId8 = string | null
export type CausationId3 = string | null
export type CorrelationId8 = string | null
export type EventId3 = string
export type EventType3 = 'user.speech_stopped'
export type GenerationId12 = string | null
export type OccurredAt3 = string
export type AudioBytes = number
export type AudioStreamId1 = string
export type DurationMs2 = number
export type UtteranceId1 = string
export type PrivacyLevel3 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion23 = string
export type Sequence4 = number | null
export type SessionId10 = string | null
export type SkillRunId3 = string | null
export type Source3 = string
export type TurnId9 = string | null
export type CausationId4 = string | null
export type CorrelationId9 = string | null
export type EventId4 = string
export type EventType4 = 'user.transcript_partial'
export type GenerationId13 = string | null
export type OccurredAt4 = string
export type IsFinal = boolean
export type Language = string | null
export type Provider = string
export type Text3 = string
export type UtteranceId2 = string
export type PrivacyLevel4 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion24 = string
export type Sequence5 = number | null
export type SessionId11 = string | null
export type SkillRunId4 = string | null
export type Source4 = string
export type TurnId10 = string | null
export type CausationId5 = string | null
export type CorrelationId10 = string | null
export type EventId5 = string
export type EventType5 = 'user.transcript_final'
export type GenerationId14 = string | null
export type OccurredAt5 = string
export type PrivacyLevel5 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion25 = string
export type Sequence6 = number | null
export type SessionId12 = string | null
export type SkillRunId5 = string | null
export type Source5 = string
export type TurnId11 = string | null
export type CausationId6 = string | null
export type CorrelationId11 = string | null
export type EventId6 = string
export type EventType6 = 'assistant.generation_started'
export type GenerationId15 = string | null
export type OccurredAt6 = string
export type BackendKind = string
export type PrivacyLevel6 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion26 = string
export type Sequence7 = number | null
export type SessionId13 = string | null
export type SkillRunId6 = string | null
export type Source6 = string
export type TurnId12 = string | null
export type CausationId7 = string | null
export type CorrelationId12 = string | null
export type EventId7 = string
export type EventType7 = 'assistant.playback_started'
export type GenerationId16 = string | null
export type OccurredAt7 = string
export type BufferedMs1 = number
export type ClientClockMs1 = number
export type PlayedPtsMs1 = number
export type SegmentId1 = string
export type StreamId2 = string
export type Transport1 = 'audio_element' | 'webrtc'
export type PrivacyLevel7 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion27 = string
export type Sequence8 = number | null
export type SessionId14 = string | null
export type SkillRunId7 = string | null
export type Source7 = string
export type TurnId13 = string | null
export type CausationId8 = string | null
export type CorrelationId13 = string | null
export type EventId8 = string
export type EventType8 = 'assistant.playback_progress'
export type GenerationId17 = string | null
export type OccurredAt8 = string
export type PrivacyLevel8 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion28 = string
export type Sequence9 = number | null
export type SessionId15 = string | null
export type SkillRunId8 = string | null
export type Source8 = string
export type TurnId14 = string | null
export type CausationId9 = string | null
export type CorrelationId14 = string | null
export type EventId9 = string
export type EventType9 = 'assistant.playback_stopped'
export type GenerationId18 = string | null
export type OccurredAt9 = string
export type BufferedMs2 = number
export type ClientClockMs2 = number
export type Completed = boolean
export type PlayedPtsMs2 = number
export type Reason4 = 'ended' | 'interrupted' | 'error' | 'queue_cleared'
export type SegmentId2 = string
export type StreamId3 = string
export type Transport2 = 'audio_element' | 'webrtc'
export type PrivacyLevel9 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion29 = string
export type Sequence10 = number | null
export type SessionId16 = string | null
export type SkillRunId9 = string | null
export type Source9 = string
export type TurnId15 = string | null
export type CausationId10 = string | null
export type CorrelationId15 = string | null
export type EventId10 = string
export type EventType10 = 'assistant.spoken_text_committed'
export type GenerationId19 = string | null
export type OccurredAt10 = string
export type SegmentId3 = string
export type SpokenText = string
export type StreamId4 = string
export type Text4 = string
export type PrivacyLevel10 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion30 = string
export type Sequence11 = number | null
export type SessionId17 = string | null
export type SkillRunId10 = string | null
export type Source10 = string
export type TurnId16 = string | null
export type CausationId11 = string | null
export type CorrelationId16 = string | null
export type EventId11 = string
export type EventType11 = 'avatar.cue_emitted'
export type GenerationId20 = string | null
export type OccurredAt11 = string
export type PrivacyLevel11 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion31 = string
export type Sequence12 = number | null
export type SessionId18 = string | null
export type SkillRunId11 = string | null
export type Source11 = string
export type TurnId17 = string | null
export type CausationId12 = string | null
export type CorrelationId17 = string | null
export type EventId12 = string
export type EventType12 = 'system.error_raised'
export type GenerationId21 = string | null
export type OccurredAt12 = string
export type PrivacyLevel12 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion32 = string
export type Sequence13 = number | null
export type SessionId19 = string | null
export type SkillRunId12 = string | null
export type Source12 = string
export type TurnId18 = string | null
export type CausationId13 = string | null
export type CorrelationId18 = string | null
export type EventId13 = string
export type GenericCoreEventType =
  | 'system.runtime_started'
  | 'system.runtime_stopping'
  | 'system.component_health_changed'
  | 'session.closed'
  | 'session.data_reset'
  | 'session.state_changed'
  | 'user.speech_progress'
  | 'assistant.text_delta'
  | 'assistant.text_segment_committed'
  | 'assistant.generation_cancelled'
  | 'assistant.generation_completed'
  | 'assistant.audio_stream_started'
  | 'assistant.audio_chunk_queued'
  | 'conversation.interruption_requested'
  | 'conversation.interrupted'
  | 'conversation.recovered'
  | 'skill.discovered'
  | 'skill.activated'
  | 'skill.run_started'
  | 'skill.progress'
  | 'skill.confirmation_requested'
  | 'skill.run_completed'
  | 'skill.run_failed'
  | 'skill.run_cancelled'
  | 'skill.run_expired'
  | 'tool.call_started'
  | 'tool.call_completed'
  | 'tool.call_failed'
  | 'memory.proposed'
  | 'memory.committed'
  | 'memory.superseded'
  | 'memory.tombstoned'
  | 'memory.recalled'
  | 'memory.extraction_completed'
  | 'character.state_changed'
  | 'character.response_planned'
  | 'character.prompt_compiled'
  | 'relationship.state_changed'
  | 'avatar.interaction_received'
  | 'model.route_selected'
  | 'model.worker_loaded'
  | 'model.worker_unloaded'
  | 'model.fallback_triggered'
  | 'voice.wake_detected'
  | 'voice.utterance_ignored'
  | 'companion.proactive_triggered'
  | 'companion.proactive_deferred'
  | 'channel.delivery_acknowledged'
  | 'resource.models_slept'
  | 'resource.models_woke'
export type GenerationId22 = string | null
export type OccurredAt13 = string
export type PrivacyLevel13 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion33 = string
export type Sequence14 = number | null
export type SessionId20 = string | null
export type SkillRunId13 = string | null
export type Source13 = string
export type TurnId19 = string | null
export type BackendKind1 = string
export type CompletedAt1 = string | null
export type GenerationId23 = string
export type InvalidatedAt = string | null
export type SessionId21 = string
export type StartedAt = string | null
export type GenerationState = 'created' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed'
export type TurnId20 = string
export type ConnectionId5 = string
export type DiscoveredAt = string | null
export type Arguments = JsonObject[]
export type Description1 = string | null
export type Name3 = string
export type Title = string | null
export type Prompts = McpPromptDescriptor[]
export type ProtocolVersion = string | null
export type Description2 = string | null
export type MimeType = string | null
export type Name4 = string
export type Title1 = string | null
export type UriTemplate = string
export type ResourceTemplates = McpResourceTemplateDescriptor[]
export type Description3 = string | null
export type MimeType1 = string | null
export type Name5 = string
export type Title2 = string | null
export type Uri = string
export type Resources = McpResourceDescriptor[]
export type ServerName = string | null
export type ServerVersion = string | null
export type Description4 = string | null
export type Name6 = string
export type Title3 = string | null
export type Tools = McpToolDescriptor[]
export type AllowRemote = boolean
export type BearerTokenConfigured = boolean
/**
 * @maxItems 32
 */
export type Command1 = string[]
export type ConnectionId6 = string
export type CreatedAt4 = string
export type Enabled1 = boolean
export type LastError = string | null
export type LastTestedAt = string | null
export type Name7 = string
export type NetworkPolicy = 'deny' | 'loopback' | 'allow'
export type SandboxBackend = string | null
export type SandboxLimitsEnforced = string[]
export type SandboxMode = 'required' | 'preferred' | 'disabled'
export type Status1 = 'untested' | 'ready' | 'error' | 'disabled'
export type TimeoutSeconds1 = number
export type Transport3 = 'stdio' | 'streamable_http' | 'sse'
export type TrustLevel = 'trusted' | 'untrusted'
export type UpdatedAt6 = string
export type Url = string | null
export type AllowRemote1 = boolean
/**
 * @maxItems 32
 */
export type Command2 = string[]
export type ConnectionId7 = string
export type Enabled2 = boolean
export type Name8 = string
export type NetworkPolicy1 = 'deny' | 'loopback' | 'allow'
export type SandboxMode1 = 'required' | 'preferred' | 'disabled'
export type TimeoutSeconds2 = number
export type Transport4 = 'stdio' | 'streamable_http' | 'sse'
export type TrustLevel1 = 'trusted' | 'untrusted'
export type Url1 = string | null
export type Confidence = number
export type CreatedAt5 = string
export type Importance = number
export type MemoryKind =
  | 'core'
  | 'semantic.fact'
  | 'semantic.preference'
  | 'episodic.shared_event'
  | 'procedural.preference'
  | 'relationship.signal'
  | 'prospective.commitment'
  | 'character.self'
export type MemoryId = string
export type Namespace = string
export type ObservedAt = string
export type Pinned = boolean
export type Predicate = string | null
export type PrivacyLevel14 = 'public' | 'local' | 'private' | 'sensitive'
/**
 * @minItems 1
 */
export type SourceEventIds = [string, ...string[]]
export type SubjectId = string | null
export type Supersedes = string | null
export type Text5 = string
export type UpdatedAt7 = string
export type ValidFrom = string | null
export type ValidTo = string | null
export type AccountKey4 = string | null
export type ChatType = 'direct' | 'group'
export type ConnectionId8 = string
export type ConversationKey3 = string
export type ConversationLabel3 = string | null
export type PrincipalScope5 = string
export type ProviderId4 = string
export type ReceivedAt1 = string
export type SchemaVersion34 = '1.0'
export type SenderDisplayName3 = string | null
export type SenderKey3 = string
export type ChannelAttributions = MemoryChannelAttribution[]
export type EntityRelevance = number
export type LexicalRelevance = number
export type MemoryId1 = string
export type Relevance = number
export type MemoryRetrievalSource = 'pinned' | 'fts' | 'semantic' | 'temporal' | 'recent'
export type RetrievalSources = MemoryRetrievalSource[]
export type SemanticRelevance = number
/**
 * @minItems 1
 */
export type SourceEventIds1 = [string, ...string[]]
export type TemporalRelevance = number
export type Text6 = string
export type OpenCommitments = MemoryExcerpt[]
export type PinnedFacts = MemoryExcerpt[]
export type ProvenanceIds = string[]
export type RecentEpisodes = MemoryExcerpt[]
export type RelationshipContext = MemoryExcerpt[]
export type RelevantMemories = MemoryExcerpt[]
export type TokenBudgetUsed = number
export type Confidence1 = number
export type Importance1 = number
export type Namespace1 = string
export type ObservedAt1 = string
export type Predicate1 = string | null
export type PrivacyLevel15 = 'public' | 'local' | 'private' | 'sensitive'
export type SubjectId1 = string | null
export type Text7 = string
export type Confidence2 = number
export type CreatedAt6 = string
export type DecidedAt = string | null
/**
 * @minItems 1
 */
export type EvidenceEventIds = [string, ...string[]]
export type MemoryOperation = 'add' | 'update' | 'supersede' | 'contradict' | 'forget' | 'ignore'
export type ProposalId = string
export type Rationale = string
export type TargetMemoryId = string | null
export type CreatedAt7 = string
export type MemoryId2 = string
export type SessionId22 = string
export type SourceEventId = string
export type SourceId = string
export type SourceKind = 'user_turn' | 'assistant_spoken' | 'memory_management' | 'migration'
export type TurnId21 = string | null
export type AdapterVersion = string
export type Capabilities = string[]
export type InputModalities = string[]
export type Kind3 = string
export type Languages = string[]
export type Id = string
export type ReviewRequired = boolean
export type Local = boolean
export type ModelId = string
export type OutputModalities = string[]
export type Devices = string[]
export type EstimatedRamMb = number | null
export type EstimatedVramMb = number | null
export type ExclusiveGpu = boolean
export type StoresInput = boolean
export type DecidedAt1 = string
export type DecidedBy = string
export type Decision = 'allow_once' | 'allow_session' | 'allow_always' | 'deny'
export type Reason5 = string | null
export type RequestId = string
export type Capability = string
export type CreatedAt8 = string
export type ExpiresAt1 = string | null
export type GrantId = string
export type Permission = string
export type Principal = string
export type RevokedAt = string | null
export type Scope = 'once' | 'session' | 'always'
export type SessionId23 = string | null
export type SkillId = string
export type Capability1 = string
export type Permission1 = string
export type Principal1 = string
export type Reason6 = string
export type RequestId1 = string
export type RequestedAt2 = string
export type SideEffect = 'read' | 'write' | 'destructive' | 'external_communication' | 'device_control'
export type Description5 = string
export type Enabled3 = boolean
export type InstallPath = string
export type InstalledAt = string
export type Name9 = string
export type NetworkPolicy2 = 'deny' | 'loopback' | 'allow'
export type PluginId = string
export type SandboxBackend1 = string | null
export type SandboxLimitsEnforced1 = string[]
export type SandboxMode2 = 'required' | 'preferred' | 'disabled'
export type TrustLevel2 = 'trusted' | 'untrusted'
export type UpdatedAt8 = string
export type Version1 = string
export type Description6 = string
export type Name10 = string
export type PluginId1 = string
export type SchemaVersion35 = '1.0'
/**
 * @minItems 1
 * @maxItems 32
 */
export type Skills = [string, ...string[]]
/**
 * @minItems 1
 * @maxItems 32
 */
export type Command3 = [string, ...string[]]
export type Kind4 = 'stdio'
export type NetworkPolicy3 = 'deny' | 'loopback' | 'allow'
export type SandboxMode3 = 'required' | 'preferred' | 'disabled'
export type TrustLevel3 = 'trusted' | 'untrusted'
export type Version2 = string
export type Budget = number
export type ConversationTokens = number
export type DroppedHistoryTurns = number
export type MemoryTokens = number
export type ModelRole = 'chat' | 'memory_extraction' | 'memory_summary' | 'embedding'
export type PersonaTokens = number
export type RelationshipTokens = number
export type SafetyTokens = number
export type SceneTokens = number
export type StateTokens = number
export type Used = number
export type Expression = 'neutral' | 'happy' | 'sad' | 'angry' | 'surprised' | 'shy' | 'curious'
export type Intent = 'comfort' | 'answer' | 'celebrate' | 'reassure' | 'tease' | 'curious'
export type Motion = ('headpat' | 'stare' | 'flustered' | 'sing') | null
export type Rationale1 = string
export type ResponseLength = 'short' | 'normal'
export type Tone = 'gentle' | 'bright' | 'shy' | 'serious' | 'playful' | 'concerned'
export type BackendKind2 = string
export type CloudContextPolicy = string
export type EstimatedCost = number | null
export type FallbackChain = string[]
export type ModelId1 = string
export type ProviderId5 = string
export type ReasonCodes = string[]
export type CharacterId5 = string
export type ConversationState =
  'idle' | 'listening' | 'committing_user_turn' | 'planning' | 'generating' | 'speaking' | 'interrupting' | 'recovering'
export type CreatedAt9 = string
export type Revision5 = number
export type SessionId24 = string
export type SessionState = 'created' | 'connecting' | 'ready' | 'degraded' | 'recovering' | 'closing' | 'closed'
export type UpdatedAt9 = string
export type BackgroundAllowed = boolean
export type AdapterOperation = 'invoke' | 'resource_read' | 'prompt_get'
export type AdapterTool = string | null
export type ConfirmationRequired = boolean
export type Description7 = string
export type Name11 = string
export type RequiredPermissions = string[]
export type SideEffect1 = 'read' | 'write' | 'destructive' | 'external_communication' | 'device_control'
export type TimeoutSeconds3 = number
export type Capabilities1 = SkillCapability[]
export type Description8 = string
export type Enabled4 = boolean
export type Interruptible1 = boolean
export type McpConnectionId = string | null
export type Name12 = string
export type PluginId2 = string | null
export type SkillId1 = string
export type Source14 = 'builtin' | 'plugin' | 'mcp_connection'
export type Version3 = string
export type Background = boolean
export type Capability2 = string
export type SkillId2 = string
export type AvatarCues = AvatarCue[]
export type MemoryProposalIds = string[]
export type ProspectiveTaskIds = string[]
export type Provenance = string[]
export type SpokenSummary = string | null
export type Status2 = string
export type UiCards = JsonObject[]
export type Capability3 = string
export type CompletedAt2 = string | null
export type ConfirmationRequestId = string | null
export type CreatedAt10 = string
export type GenerationId24 = string | null
export type McpConnectionId1 = string | null
export type Origin = 'manual' | 'agent' | 'external_mcp'
export type PluginId3 = string | null
export type Progress = number | null
export type ProviderToolCallId = string | null
export type SessionId25 = string
export type SkillId3 = string
export type SkillRunId14 = string
export type SkillVersion = string
export type StartedAt1 = string | null
export type SkillRunState =
  | 'created'
  | 'activating'
  | 'running'
  | 'waiting_for_tool'
  | 'waiting_for_confirmation'
  | 'paused'
  | 'succeeded'
  | 'failed'
  | 'cancelling'
  | 'cancelled'
  | 'expired'
export type TurnId22 = string | null
export type UpdatedAt10 = string
export type ActiveSkillIds = string[]
export type CommittedAt = string | null
export type CommittedText = string | null
export type SceneSnapshotId = string | null
export type SessionId26 = string
export type TurnId23 = string
export type ByteLength1 = number
export type Codec1 = 'jpeg' | 'png' | 'h264' | 'vp8'
export type EndOfStream1 = boolean
export type GenerationId25 = string | null
export type Height = number
export type PtsMs1 = number
export type Sequence15 = number
export type StreamId5 = string
export type Width = number

/**
 * Schema-only catalog used to generate a single conflict-free TypeScript module.
 */
export interface ProtocolCatalog {
  audio_frame: AudioFrameHeader
  avatar_capabilities: AvatarCapabilityManifest
  avatar_cue: AvatarCue
  avatar_interaction: AvatarInteractionEvent
  channel_authorization: ChannelAuthorizationSnapshot
  channel_authorization_start_request: ChannelAuthorizationStartRequest
  channel_authorization_verification_request: ChannelAuthorizationVerificationRequest
  channel_connection: ChannelConnectionSnapshot
  channel_connection_configuration: ChannelConnectionConfiguration
  channel_delivery: ChannelDeliverySnapshot
  channel_delivery_acknowledgement: ChannelDeliveryAcknowledgement
  channel_delivery_claim_request: ChannelDeliveryClaimRequest
  channel_error: ChannelErrorResponse
  channel_gateway_status: ChannelGatewayStatusSnapshot
  channel_inbound_text: ChannelInboundTextMessage
  channel_provider: ChannelProviderRegistration
  channel_turn: ChannelTurnSnapshot
  channel_turn_cancel_receipt: ChannelTurnCancelReceipt
  channel_turn_cancel_request: ChannelTurnCancelRequest
  channel_turn_receipt: ChannelTurnReceipt
  character_kernel: CharacterKernelSnapshot
  command: Command
  conversation_interruption: ConversationInterruption
  error: StructuredError
  event: EventModel
  generation: GenerationSnapshot
  mcp_capabilities: McpCapabilitySnapshot
  mcp_connection: McpConnectionSnapshot
  mcp_connection_configuration: McpConnectionConfiguration
  memory: MemoryRecord
  memory_channel_attribution: MemoryChannelAttribution
  memory_context: MemoryContextPacket
  memory_proposal: MemoryProposal
  memory_source: MemorySource
  model: ModelManifest
  permission_decision: PermissionDecision
  permission_grant: PermissionGrant
  permission_request: PermissionRequest
  plugin: PluginSnapshot
  plugin_manifest: PluginManifest
  prompt_budget: PromptBudgetReport
  response_plan: ResponsePlan
  route: RouteDecision
  session: SessionSnapshot
  skill: SkillDefinition
  skill_invocation: SkillInvocation
  skill_result: SkillResult
  skill_run: SkillRunSnapshot
  turn: TurnSnapshot
  video_frame: VideoFrameHeader
  [k: string]: unknown
}
export interface AudioFrameHeader {
  byte_length: ByteLength
  channels: Channels
  codec: Codec
  duration_ms: DurationMs
  end_of_stream?: EndOfStream
  generation_id?: GenerationId
  pts_ms: PtsMs
  sample_rate: SampleRate
  sequence: Sequence
  stream_id: StreamId
  [k: string]: unknown
}
export interface AvatarCapabilityManifest {
  avatar_id: AvatarId
  expressions?: Expressions
  gaze_targets?: GazeTargets
  hit_areas?: HitAreas
  motions?: Motions
  renderer_kind: RendererKind
  states?: States
  supports_lipsync?: SupportsLipsync
  [k: string]: unknown
}
export interface AvatarCue {
  cue_id: CueId
  duration_ms?: DurationMs1
  generation_id?: GenerationId1
  intensity?: Intensity
  interruptible?: Interruptible
  kind: Kind
  metadata?: JsonObject
  name: Name
  priority?: Priority
  start_anchor?: StartAnchor
  [k: string]: unknown
}
export interface JsonObject {
  [k: string]: JsonValue
}
export interface AvatarInteractionEvent {
  avatar_id: AvatarId1
  interaction_id: InteractionId
  kind: Kind1
  metadata?: JsonObject
  target?: Target
  x?: X
  y?: Y
  [k: string]: unknown
}
/**
 * Sanitized state of a short-lived provider authorization session.
 */
export interface ChannelAuthorizationSnapshot {
  auth_session_id: AuthSessionId
  connection?: ChannelConnectionSnapshot | null
  created_at: CreatedAt1
  error?: StructuredError | null
  expires_at: ExpiresAt
  method?: ChannelAuthorizationMethod1
  poll_after_ms?: PollAfterMs
  provider_id: ProviderId1
  qr_code_content?: QrCodeContent
  schema_version?: SchemaVersion2
  status: ChannelAuthorizationStatus
  status_message?: StatusMessage
  updated_at: UpdatedAt1
  verification_required?: VerificationRequired
  [k: string]: unknown
}
/**
 * Persisted connection state safe to expose to local settings clients.
 *
 * ``account_key`` and ``allowed_sender_keys`` remain local, non-secret,
 * provider-scoped identifiers. They are never credentials and should not be
 * forwarded to a conversation peer.
 */
export interface ChannelConnectionSnapshot {
  capabilities?: ChannelProviderCapabilities
  configuration: ChannelConnectionConfiguration
  created_at: CreatedAt
  last_error?: StructuredError | null
  last_seen_at?: LastSeenAt
  revision: Revision
  schema_version?: SchemaVersion1
  status?: ChannelConnectionStatus
  updated_at: UpdatedAt
  [k: string]: unknown
}
/**
 * Transport behavior advertised by one provider adapter.
 *
 * The first provider advertises only direct text by default. ``group`` is a
 * protocol capability for future adapters, but a Runtime must reject it until
 * its provider registration and local policy explicitly enable group chat.
 */
export interface ChannelProviderCapabilities {
  authorization_methods?: AuthorizationMethods
  chat_types?: ChatTypes
  inbound_message_kinds?: InboundMessageKinds
  max_text_chars?: MaxTextChars
  outbound_message_kinds?: OutboundMessageKinds
  supports_cancellation?: SupportsCancellation
  supports_delivery_ack?: SupportsDeliveryAck
  supports_partial_replies?: SupportsPartialReplies
  supports_proactive_messages?: SupportsProactiveMessages
  supports_typing?: SupportsTyping
  [k: string]: unknown
}
/**
 * Non-secret Runtime configuration for one external channel account.
 *
 * ``principal_scope`` is the Runtime-owned privacy, relationship, and memory
 * isolation key. It is not a provider user id and must never be derived from
 * message text. Provider credentials and login state are intentionally absent.
 */
export interface ChannelConnectionConfiguration {
  account_key?: AccountKey
  allowed_sender_keys?: AllowedSenderKeys
  character_id: CharacterId
  connection_id: ConnectionId
  enabled?: Enabled
  name: Name1
  principal_scope: PrincipalScope
  provider_id: ProviderId
  schema_version?: SchemaVersion
  timeout_seconds?: TimeoutSeconds
  [k: string]: unknown
}
export interface StructuredError {
  code: Code
  component: Component
  correlation_id?: CorrelationId
  details?: JsonObject
  message: Message
  retryable: Retryable
  [k: string]: unknown
}
/**
 * Start one local, short-lived provider authorization session.
 *
 * Provider credentials and account identities must never be accepted from the
 * browser. A successful adapter authorization derives them and returns only a
 * sanitized ``ChannelConnectionSnapshot``.
 */
export interface ChannelAuthorizationStartRequest {
  character_id: CharacterId1
  connection_name?: ConnectionName
  method?: ChannelAuthorizationMethod2
  principal_scope?: PrincipalScope1
  provider_id: ProviderId2
  schema_version?: SchemaVersion3
  [k: string]: unknown
}
/**
 * Submit a short pairing code requested by the provider authorization flow.
 */
export interface ChannelAuthorizationVerificationRequest {
  schema_version?: SchemaVersion4
  verification_code: VerificationCode
  [k: string]: unknown
}
export interface ChannelDeliverySnapshot {
  attempt?: Attempt
  channel_turn_id: ChannelTurnId
  connection_id: ConnectionId1
  created_at: CreatedAt2
  delivered_at?: DeliveredAt
  delivery_id: DeliveryId
  last_error?: StructuredError | null
  lease_expires_at?: LeaseExpiresAt
  lease_id?: LeaseId
  provider_message_id?: ProviderMessageId
  schema_version?: SchemaVersion5
  status: ChannelDeliveryStatus
  updated_at: UpdatedAt2
  [k: string]: unknown
}
/**
 * Idempotent adapter acknowledgement for one outbound delivery.
 */
export interface ChannelDeliveryAcknowledgement {
  acknowledged_at: AcknowledgedAt
  channel_turn_id: ChannelTurnId1
  delivery_id: DeliveryId1
  error?: StructuredError | null
  lease_id: LeaseId1
  provider_message_id?: ProviderMessageId1
  schema_version?: SchemaVersion6
  status: Status
  [k: string]: unknown
}
/**
 * Acquire a short server-timed lease before invoking a provider send API.
 */
export interface ChannelDeliveryClaimRequest {
  channel_turn_id: ChannelTurnId2
  delivery_id: DeliveryId2
  lease_id: LeaseId2
  lease_seconds?: LeaseSeconds
  schema_version?: SchemaVersion7
  [k: string]: unknown
}
/**
 * Normalized gateway failure safe for HTTP and plugin boundaries.
 */
export interface ChannelErrorResponse {
  channel_turn_id?: ChannelTurnId3
  error: StructuredError
  external_message_id?: ExternalMessageId
  retry_after_ms?: RetryAfterMs
  schema_version?: SchemaVersion8
  [k: string]: unknown
}
/**
 * Aggregate health for the provider-neutral channel gateway.
 */
export interface ChannelGatewayStatusSnapshot {
  checked_at: CheckedAt
  enabled_connection_count: EnabledConnectionCount
  provider_count: ProviderCount
  schema_version?: SchemaVersion9
  status: ChannelGatewayStatus
  [k: string]: unknown
}
/**
 * Normalized text message admitted by a trusted adapter.
 *
 * Identity fields are opaque and provider-scoped:
 *
 * * ``external_message_id`` identifies one inbound message and is the
 *   idempotency key within a connection. It must remain stable across retries.
 * * ``conversation_key`` identifies the provider conversation, not a Runtime
 *   session id.
 * * ``sender_key`` identifies the provider peer, not a display name.
 * * ``principal_scope`` is assigned by Runtime connection policy and must
 *   equal the configured connection scope; adapters must not accept it from
 *   untrusted user content.
 *
 * ``conversation_label`` and ``sender_display_name`` are untrusted display
 * hints only. They must never be used for identity, authorization, memory
 * scope, prompt instructions, or idempotency.
 */
export interface ChannelInboundTextMessage {
  account_key?: AccountKey1
  chat_type?: ChannelChatType1
  connection_id: ConnectionId2
  conversation_key: ConversationKey
  conversation_label?: ConversationLabel
  external_message_id: ExternalMessageId1
  kind?: Kind2
  principal_scope: PrincipalScope2
  received_at: ReceivedAt
  reply_to_external_message_id?: ReplyToExternalMessageId
  schema_version?: SchemaVersion10
  sender_display_name?: SenderDisplayName
  sender_key: SenderKey
  text: Text
  [k: string]: unknown
}
/**
 * Discoverable definition for a provider-neutral channel adapter.
 */
export interface ChannelProviderRegistration {
  capabilities?: ChannelProviderCapabilities
  description: Description
  name: Name2
  provider_id: ProviderId3
  schema_version?: SchemaVersion11
  version: Version
  [k: string]: unknown
}
/**
 * Current durable result for one normalized inbound message.
 */
export interface ChannelTurnSnapshot {
  account_key?: AccountKey2
  channel_turn_id: ChannelTurnId4
  chat_type?: ChannelChatType2
  completed_at?: CompletedAt
  connection_id: ConnectionId3
  conversation_key: ConversationKey1
  conversation_label?: ConversationLabel1
  created_at: CreatedAt3
  delivery_id?: DeliveryId3
  delivery_status?: ChannelDeliveryStatus | null
  error?: StructuredError | null
  external_message_id: ExternalMessageId2
  generation_id: GenerationId2
  principal_scope: PrincipalScope3
  reply_text?: ReplyText
  revision: Revision1
  schema_version?: SchemaVersion12
  sender_display_name?: SenderDisplayName1
  sender_key: SenderKey1
  session_id: SessionId
  status: ChannelTurnStatus
  turn_id: TurnId
  updated_at: UpdatedAt3
  [k: string]: unknown
}
export interface ChannelTurnCancelReceipt {
  accepted: Accepted
  acknowledged_at: AcknowledgedAt1
  channel_turn_id: ChannelTurnId5
  revision: Revision2
  schema_version?: SchemaVersion13
  status: ChannelTurnStatus
  [k: string]: unknown
}
export interface ChannelTurnCancelRequest {
  reason: Reason
  requested_at: RequestedAt
  schema_version?: SchemaVersion14
  [k: string]: unknown
}
/**
 * Admission receipt returned before or while a channel turn is processed.
 */
export interface ChannelTurnReceipt {
  accepted_at: AcceptedAt
  account_key?: AccountKey3
  channel_turn_id: ChannelTurnId6
  chat_type?: ChannelChatType3
  connection_id: ConnectionId4
  conversation_key: ConversationKey2
  conversation_label?: ConversationLabel2
  duplicate?: Duplicate
  external_message_id: ExternalMessageId3
  generation_id: GenerationId3
  poll_after_ms?: PollAfterMs1
  principal_scope: PrincipalScope4
  revision: Revision3
  schema_version?: SchemaVersion15
  sender_display_name?: SenderDisplayName2
  sender_key: SenderKey2
  session_id: SessionId1
  status: ChannelTurnStatus
  turn_id: TurnId1
  [k: string]: unknown
}
export interface CharacterKernelSnapshot {
  affect: AffectState
  character_id: CharacterId2
  relationship: RelationshipState
  revision: Revision4
  user_scope: UserScope
  [k: string]: unknown
}
export interface AffectState {
  arousal?: Arousal
  attention?: Attention
  embarrassment?: Embarrassment
  energy?: Energy
  tension?: Tension
  updated_at: UpdatedAt4
  valence?: Valence
  [k: string]: unknown
}
export interface RelationshipState {
  affinity?: Affinity
  comfort?: Comfort
  familiarity?: Familiarity
  interaction_count?: InteractionCount
  preferred_address?: PreferredAddress
  recent_tension?: RecentTension
  stage?: Stage
  trust?: Trust
  updated_at: UpdatedAt5
  [k: string]: unknown
}
export interface SessionStartCommand {
  command_id: CommandId
  command_type?: CommandType
  correlation_id?: CorrelationId1
  expected_revision?: ExpectedRevision
  generation_id?: GenerationId4
  issued_at: IssuedAt
  issuer: Issuer
  payload: SessionStartPayload
  schema_version?: SchemaVersion16
  session_id?: SessionId2
  turn_id?: TurnId2
  [k: string]: unknown
}
export interface SessionStartPayload {
  character_id: CharacterId3
  [k: string]: unknown
}
export interface TextSendCommand {
  command_id: CommandId1
  command_type?: CommandType1
  correlation_id?: CorrelationId2
  expected_revision?: ExpectedRevision1
  generation_id?: GenerationId5
  issued_at: IssuedAt1
  issuer: Issuer1
  payload: TextSendPayload
  schema_version?: SchemaVersion17
  session_id?: SessionId3
  turn_id?: TurnId3
  [k: string]: unknown
}
export interface TextSendPayload {
  text: Text1
  [k: string]: unknown
}
export interface ConversationInterruptCommand {
  command_id: CommandId2
  command_type?: CommandType2
  correlation_id?: CorrelationId3
  expected_revision?: ExpectedRevision2
  generation_id?: GenerationId6
  issued_at: IssuedAt2
  issuer: Issuer2
  payload: ConversationInterruptPayload
  schema_version?: SchemaVersion18
  session_id?: SessionId4
  turn_id?: TurnId4
  [k: string]: unknown
}
export interface ConversationInterruptPayload {
  reason?: Reason1
  [k: string]: unknown
}
export interface PlaybackAckCommand {
  command_id: CommandId3
  command_type?: CommandType3
  correlation_id?: CorrelationId4
  expected_revision?: ExpectedRevision3
  generation_id?: GenerationId7
  issued_at: IssuedAt3
  issuer: Issuer3
  payload: PlaybackAckPayload
  schema_version?: SchemaVersion19
  session_id?: SessionId5
  turn_id?: TurnId5
  [k: string]: unknown
}
export interface PlaybackAckPayload {
  buffered_ms: BufferedMs
  client_clock_ms: ClientClockMs
  phase: Phase
  played_pts_ms: PlayedPtsMs
  reason?: Reason2
  segment_id: SegmentId
  stream_id: StreamId1
  transport: Transport
  [k: string]: unknown
}
export interface ConversationInterruption {
  generation_id: GenerationId8
  initiated_by: InterruptionInitiator
  interruption_id: InterruptionId
  reason: Reason3
  requested_at: RequestedAt1
  session_id: SessionId6
  [k: string]: unknown
}
export interface SessionCreatedEvent {
  causation_id?: CausationId
  correlation_id?: CorrelationId5
  event_id: EventId
  event_type?: EventType
  generation_id?: GenerationId9
  occurred_at: OccurredAt
  payload: SessionCreatedPayload
  privacy?: PrivacyLevel
  schema_version?: SchemaVersion20
  sequence?: Sequence1
  session_id?: SessionId7
  skill_run_id?: SkillRunId
  source: Source
  turn_id?: TurnId6
  [k: string]: unknown
}
export interface SessionCreatedPayload {
  character_id: CharacterId4
  [k: string]: unknown
}
export interface UserTurnCommittedEvent {
  causation_id?: CausationId1
  correlation_id?: CorrelationId6
  event_id: EventId1
  event_type?: EventType1
  generation_id?: GenerationId10
  occurred_at: OccurredAt1
  payload: UserTurnCommittedPayload
  privacy?: PrivacyLevel1
  schema_version?: SchemaVersion21
  sequence?: Sequence2
  session_id?: SessionId8
  skill_run_id?: SkillRunId1
  source: Source1
  turn_id?: TurnId7
  [k: string]: unknown
}
export interface UserTurnCommittedPayload {
  text: Text2
  [k: string]: unknown
}
export interface UserSpeechStartedEvent {
  causation_id?: CausationId2
  correlation_id?: CorrelationId7
  event_id: EventId2
  event_type?: EventType2
  generation_id?: GenerationId11
  occurred_at: OccurredAt2
  payload: UserSpeechStartedPayload
  privacy?: PrivacyLevel2
  schema_version?: SchemaVersion22
  sequence?: Sequence3
  session_id?: SessionId9
  skill_run_id?: SkillRunId2
  source: Source2
  turn_id?: TurnId8
  [k: string]: unknown
}
export interface UserSpeechStartedPayload {
  audio_stream_id: AudioStreamId
  channels: Channels1
  sample_rate: SampleRate1
  utterance_id: UtteranceId
  [k: string]: unknown
}
export interface UserSpeechStoppedEvent {
  causation_id?: CausationId3
  correlation_id?: CorrelationId8
  event_id: EventId3
  event_type?: EventType3
  generation_id?: GenerationId12
  occurred_at: OccurredAt3
  payload: UserSpeechStoppedPayload
  privacy?: PrivacyLevel3
  schema_version?: SchemaVersion23
  sequence?: Sequence4
  session_id?: SessionId10
  skill_run_id?: SkillRunId3
  source: Source3
  turn_id?: TurnId9
  [k: string]: unknown
}
export interface UserSpeechStoppedPayload {
  audio_bytes: AudioBytes
  audio_stream_id: AudioStreamId1
  duration_ms: DurationMs2
  utterance_id: UtteranceId1
  [k: string]: unknown
}
export interface UserTranscriptPartialEvent {
  causation_id?: CausationId4
  correlation_id?: CorrelationId9
  event_id: EventId4
  event_type?: EventType4
  generation_id?: GenerationId13
  occurred_at: OccurredAt4
  payload: UserTranscriptPayload
  privacy?: PrivacyLevel4
  schema_version?: SchemaVersion24
  sequence?: Sequence5
  session_id?: SessionId11
  skill_run_id?: SkillRunId4
  source: Source4
  turn_id?: TurnId10
  [k: string]: unknown
}
export interface UserTranscriptPayload {
  is_final: IsFinal
  language?: Language
  provider: Provider
  text: Text3
  utterance_id: UtteranceId2
  [k: string]: unknown
}
export interface UserTranscriptFinalEvent {
  causation_id?: CausationId5
  correlation_id?: CorrelationId10
  event_id: EventId5
  event_type?: EventType5
  generation_id?: GenerationId14
  occurred_at: OccurredAt5
  payload: UserTranscriptPayload
  privacy?: PrivacyLevel5
  schema_version?: SchemaVersion25
  sequence?: Sequence6
  session_id?: SessionId12
  skill_run_id?: SkillRunId5
  source: Source5
  turn_id?: TurnId11
  [k: string]: unknown
}
export interface AssistantGenerationStartedEvent {
  causation_id?: CausationId6
  correlation_id?: CorrelationId11
  event_id: EventId6
  event_type?: EventType6
  generation_id?: GenerationId15
  occurred_at: OccurredAt6
  payload: AssistantGenerationStartedPayload
  privacy?: PrivacyLevel6
  schema_version?: SchemaVersion26
  sequence?: Sequence7
  session_id?: SessionId13
  skill_run_id?: SkillRunId6
  source: Source6
  turn_id?: TurnId12
  [k: string]: unknown
}
export interface AssistantGenerationStartedPayload {
  backend_kind: BackendKind
  [k: string]: unknown
}
export interface AssistantPlaybackStartedEvent {
  causation_id?: CausationId7
  correlation_id?: CorrelationId12
  event_id: EventId7
  event_type?: EventType7
  generation_id?: GenerationId16
  occurred_at: OccurredAt7
  payload: AssistantPlaybackPayload
  privacy?: PrivacyLevel7
  schema_version?: SchemaVersion27
  sequence?: Sequence8
  session_id?: SessionId14
  skill_run_id?: SkillRunId7
  source: Source7
  turn_id?: TurnId13
  [k: string]: unknown
}
export interface AssistantPlaybackPayload {
  buffered_ms: BufferedMs1
  client_clock_ms: ClientClockMs1
  played_pts_ms: PlayedPtsMs1
  segment_id: SegmentId1
  stream_id: StreamId2
  transport: Transport1
  [k: string]: unknown
}
export interface AssistantPlaybackProgressEvent {
  causation_id?: CausationId8
  correlation_id?: CorrelationId13
  event_id: EventId8
  event_type?: EventType8
  generation_id?: GenerationId17
  occurred_at: OccurredAt8
  payload: AssistantPlaybackPayload
  privacy?: PrivacyLevel8
  schema_version?: SchemaVersion28
  sequence?: Sequence9
  session_id?: SessionId15
  skill_run_id?: SkillRunId8
  source: Source8
  turn_id?: TurnId14
  [k: string]: unknown
}
export interface AssistantPlaybackStoppedEvent {
  causation_id?: CausationId9
  correlation_id?: CorrelationId14
  event_id: EventId9
  event_type?: EventType9
  generation_id?: GenerationId18
  occurred_at: OccurredAt9
  payload: AssistantPlaybackStoppedPayload
  privacy?: PrivacyLevel9
  schema_version?: SchemaVersion29
  sequence?: Sequence10
  session_id?: SessionId16
  skill_run_id?: SkillRunId9
  source: Source9
  turn_id?: TurnId15
  [k: string]: unknown
}
export interface AssistantPlaybackStoppedPayload {
  buffered_ms: BufferedMs2
  client_clock_ms: ClientClockMs2
  completed: Completed
  played_pts_ms: PlayedPtsMs2
  reason: Reason4
  segment_id: SegmentId2
  stream_id: StreamId3
  transport: Transport2
  [k: string]: unknown
}
export interface AssistantSpokenTextCommittedEvent {
  causation_id?: CausationId10
  correlation_id?: CorrelationId15
  event_id: EventId10
  event_type?: EventType10
  generation_id?: GenerationId19
  occurred_at: OccurredAt10
  payload: AssistantSpokenTextCommittedPayload
  privacy?: PrivacyLevel10
  schema_version?: SchemaVersion30
  sequence?: Sequence11
  session_id?: SessionId17
  skill_run_id?: SkillRunId10
  source: Source10
  turn_id?: TurnId16
  [k: string]: unknown
}
export interface AssistantSpokenTextCommittedPayload {
  segment_id: SegmentId3
  spoken_text: SpokenText
  stream_id: StreamId4
  text: Text4
  [k: string]: unknown
}
export interface AvatarCueEmittedEvent {
  causation_id?: CausationId11
  correlation_id?: CorrelationId16
  event_id: EventId11
  event_type?: EventType11
  generation_id?: GenerationId20
  occurred_at: OccurredAt11
  payload: AvatarCueEmittedPayload
  privacy?: PrivacyLevel11
  schema_version?: SchemaVersion31
  sequence?: Sequence12
  session_id?: SessionId18
  skill_run_id?: SkillRunId11
  source: Source11
  turn_id?: TurnId17
  [k: string]: unknown
}
export interface AvatarCueEmittedPayload {
  cue: AvatarCue
  [k: string]: unknown
}
export interface ErrorRaisedEvent {
  causation_id?: CausationId12
  correlation_id?: CorrelationId17
  event_id: EventId12
  event_type?: EventType12
  generation_id?: GenerationId21
  occurred_at: OccurredAt12
  payload: ErrorRaisedPayload
  privacy?: PrivacyLevel12
  schema_version?: SchemaVersion32
  sequence?: Sequence13
  session_id?: SessionId19
  skill_run_id?: SkillRunId12
  source: Source12
  turn_id?: TurnId18
  [k: string]: unknown
}
export interface ErrorRaisedPayload {
  error: StructuredError
  [k: string]: unknown
}
/**
 * Known lower-value v1 event whose payload will be specialized before its phase begins.
 */
export interface GenericCoreEvent {
  causation_id?: CausationId13
  correlation_id?: CorrelationId18
  event_id: EventId13
  event_type: GenericCoreEventType
  generation_id?: GenerationId22
  occurred_at: OccurredAt13
  payload: JsonObject
  privacy?: PrivacyLevel13
  schema_version?: SchemaVersion33
  sequence?: Sequence14
  session_id?: SessionId20
  skill_run_id?: SkillRunId13
  source: Source13
  turn_id?: TurnId19
  [k: string]: unknown
}
export interface GenerationSnapshot {
  backend_kind: BackendKind1
  completed_at?: CompletedAt1
  generation_id: GenerationId23
  invalidated_at?: InvalidatedAt
  session_id: SessionId21
  started_at?: StartedAt
  state: GenerationState
  turn_id: TurnId20
  [k: string]: unknown
}
export interface McpCapabilitySnapshot {
  connection_id: ConnectionId5
  discovered_at?: DiscoveredAt
  prompts?: Prompts
  protocol_version?: ProtocolVersion
  resource_templates?: ResourceTemplates
  resources?: Resources
  server_name?: ServerName
  server_version?: ServerVersion
  tools?: Tools
  [k: string]: unknown
}
export interface McpPromptDescriptor {
  arguments?: Arguments
  description?: Description1
  name: Name3
  title?: Title
  [k: string]: unknown
}
export interface McpResourceTemplateDescriptor {
  description?: Description2
  mime_type?: MimeType
  name: Name4
  title?: Title1
  uri_template: UriTemplate
  [k: string]: unknown
}
export interface McpResourceDescriptor {
  description?: Description3
  mime_type?: MimeType1
  name: Name5
  title?: Title2
  uri: Uri
  [k: string]: unknown
}
export interface McpToolDescriptor {
  description?: Description4
  input_schema?: JsonObject
  name: Name6
  output_schema?: JsonObject | null
  title?: Title3
  [k: string]: unknown
}
export interface McpConnectionSnapshot {
  allow_remote?: AllowRemote
  bearer_token_configured?: BearerTokenConfigured
  capabilities: McpCapabilitySnapshot
  command?: Command1
  connection_id: ConnectionId6
  created_at: CreatedAt4
  enabled?: Enabled1
  last_error?: LastError
  last_tested_at?: LastTestedAt
  name: Name7
  network_policy?: NetworkPolicy
  sandbox_backend?: SandboxBackend
  sandbox_limits_enforced?: SandboxLimitsEnforced
  sandbox_mode?: SandboxMode
  status?: Status1
  timeout_seconds?: TimeoutSeconds1
  transport: Transport3
  trust_level?: TrustLevel
  updated_at: UpdatedAt6
  url?: Url
  [k: string]: unknown
}
/**
 * Persisted MCP Host connection settings; authentication secrets are excluded.
 */
export interface McpConnectionConfiguration {
  allow_remote?: AllowRemote1
  command?: Command2
  connection_id: ConnectionId7
  enabled?: Enabled2
  name: Name8
  network_policy?: NetworkPolicy1
  sandbox_mode?: SandboxMode1
  timeout_seconds?: TimeoutSeconds2
  transport: Transport4
  trust_level?: TrustLevel1
  url?: Url1
  [k: string]: unknown
}
export interface MemoryRecord {
  confidence: Confidence
  created_at: CreatedAt5
  importance: Importance
  kind: MemoryKind
  memory_id: MemoryId
  namespace: Namespace
  observed_at: ObservedAt
  pinned?: Pinned
  predicate?: Predicate
  sensitivity?: PrivacyLevel14
  source_event_ids: SourceEventIds
  state?: 'active' | 'superseded' | 'contradicted' | 'tombstoned'
  subject_id?: SubjectId
  supersedes?: Supersedes
  text: Text5
  updated_at: UpdatedAt7
  valid_from?: ValidFrom
  valid_to?: ValidTo
  value?:
    | string
    | number
    | boolean
    | JsonValue[]
    | {
        [k: string]: JsonValue
      }
    | null
  [k: string]: unknown
}
/**
 * Immutable, provider-neutral attribution for one memory source.
 *
 * Stable provider-scoped keys preserve where an observation came from after
 * the originating transcript falls out of the recent-history window. Display
 * labels are optional untrusted presentation data and never identity or
 * instructions.
 */
export interface MemoryChannelAttribution {
  account_key?: AccountKey4
  chat_type: ChatType
  connection_id: ConnectionId8
  conversation_key: ConversationKey3
  conversation_label?: ConversationLabel3
  principal_scope: PrincipalScope5
  provider_id: ProviderId4
  received_at: ReceivedAt1
  schema_version?: SchemaVersion34
  sender_display_name?: SenderDisplayName3
  sender_key: SenderKey3
  [k: string]: unknown
}
export interface MemoryContextPacket {
  open_commitments?: OpenCommitments
  pinned_facts?: PinnedFacts
  provenance_ids?: ProvenanceIds
  recent_episodes?: RecentEpisodes
  relationship_context?: RelationshipContext
  relevant_memories?: RelevantMemories
  token_budget_used: TokenBudgetUsed
  [k: string]: unknown
}
export interface MemoryExcerpt {
  channel_attributions?: ChannelAttributions
  entity_relevance?: EntityRelevance
  lexical_relevance?: LexicalRelevance
  memory_id: MemoryId1
  relevance: Relevance
  retrieval_sources?: RetrievalSources
  semantic_relevance?: SemanticRelevance
  source_event_ids: SourceEventIds1
  temporal_relevance?: TemporalRelevance
  text: Text6
  [k: string]: unknown
}
export interface MemoryProposal {
  candidate?: MemoryRecordDraft | null
  confidence: Confidence2
  created_at: CreatedAt6
  decided_at?: DecidedAt
  evidence_event_ids: EvidenceEventIds
  operation: MemoryOperation
  proposal_id: ProposalId
  rationale: Rationale
  status?: 'pending' | 'accepted' | 'rejected' | 'ignored'
  target_memory_id?: TargetMemoryId
  [k: string]: unknown
}
export interface MemoryRecordDraft {
  confidence: Confidence1
  importance: Importance1
  kind: MemoryKind
  namespace: Namespace1
  observed_at: ObservedAt1
  predicate?: Predicate1
  sensitivity?: PrivacyLevel15
  subject_id?: SubjectId1
  text: Text7
  value?:
    | string
    | number
    | boolean
    | JsonValue[]
    | {
        [k: string]: JsonValue
      }
    | null
  [k: string]: unknown
}
export interface MemorySource {
  channel_attribution?: MemoryChannelAttribution | null
  created_at: CreatedAt7
  memory_id: MemoryId2
  session_id: SessionId22
  source_event_id: SourceEventId
  source_id: SourceId
  source_kind: SourceKind
  turn_id?: TurnId21
  [k: string]: unknown
}
export interface ModelManifest {
  adapter_version: AdapterVersion
  capabilities?: Capabilities
  input_modalities?: InputModalities
  kind: Kind3
  languages?: Languages
  license: ModelLicense
  local: Local
  model_id: ModelId
  output_modalities?: OutputModalities
  resource?: ModelResourceProfile
  stores_input?: StoresInput
  [k: string]: unknown
}
export interface ModelLicense {
  id: Id
  review_required?: ReviewRequired
  [k: string]: unknown
}
export interface ModelResourceProfile {
  devices?: Devices
  estimated_ram_mb?: EstimatedRamMb
  estimated_vram_mb?: EstimatedVramMb
  exclusive_gpu?: ExclusiveGpu
  [k: string]: unknown
}
export interface PermissionDecision {
  decided_at: DecidedAt1
  decided_by: DecidedBy
  decision: Decision
  reason?: Reason5
  request_id: RequestId
  [k: string]: unknown
}
export interface PermissionGrant {
  capability: Capability
  created_at: CreatedAt8
  expires_at?: ExpiresAt1
  grant_id: GrantId
  permission: Permission
  principal: Principal
  revoked_at?: RevokedAt
  scope: Scope
  session_id?: SessionId23
  skill_id: SkillId
  [k: string]: unknown
}
export interface PermissionRequest {
  capability: Capability1
  context?: JsonObject
  permission: Permission1
  principal: Principal1
  reason: Reason6
  request_id: RequestId1
  requested_at: RequestedAt2
  side_effect: SideEffect
  [k: string]: unknown
}
export interface PluginSnapshot {
  description: Description5
  enabled: Enabled3
  install_path: InstallPath
  installed_at: InstalledAt
  name: Name9
  network_policy?: NetworkPolicy2
  plugin_id: PluginId
  sandbox_backend?: SandboxBackend1
  sandbox_limits_enforced?: SandboxLimitsEnforced1
  sandbox_mode?: SandboxMode2
  trust_level?: TrustLevel2
  updated_at: UpdatedAt8
  version: Version1
  [k: string]: unknown
}
export interface PluginManifest {
  description: Description6
  name: Name10
  plugin_id: PluginId1
  schema_version?: SchemaVersion35
  skills: Skills
  transport: PluginTransport
  version: Version2
  [k: string]: unknown
}
export interface PluginTransport {
  command: Command3
  kind?: Kind4
  network_policy?: NetworkPolicy3
  sandbox_mode?: SandboxMode3
  trust_level?: TrustLevel3
  [k: string]: unknown
}
export interface PromptBudgetReport {
  budget: Budget
  conversation_tokens: ConversationTokens
  dropped_history_turns: DroppedHistoryTurns
  memory_tokens: MemoryTokens
  model_role: ModelRole
  persona_tokens: PersonaTokens
  relationship_tokens: RelationshipTokens
  safety_tokens: SafetyTokens
  scene_tokens: SceneTokens
  state_tokens: StateTokens
  used: Used
  [k: string]: unknown
}
export interface ResponsePlan {
  expression: Expression
  intent: Intent
  motion?: Motion
  rationale: Rationale1
  response_length?: ResponseLength
  tone: Tone
  [k: string]: unknown
}
export interface RouteDecision {
  backend_kind: BackendKind2
  cloud_context_policy: CloudContextPolicy
  estimated_cost?: EstimatedCost
  fallback_chain?: FallbackChain
  model_id: ModelId1
  provider_id: ProviderId5
  reason_codes?: ReasonCodes
  [k: string]: unknown
}
export interface SessionSnapshot {
  character_id: CharacterId5
  conversation_state: ConversationState
  created_at: CreatedAt9
  revision: Revision5
  session_id: SessionId24
  state: SessionState
  updated_at: UpdatedAt9
  [k: string]: unknown
}
export interface SkillDefinition {
  background_allowed?: BackgroundAllowed
  capabilities?: Capabilities1
  description: Description8
  enabled?: Enabled4
  interruptible?: Interruptible1
  mcp_connection_id?: McpConnectionId
  name: Name12
  plugin_id?: PluginId2
  skill_id: SkillId1
  source?: Source14
  version: Version3
  [k: string]: unknown
}
export interface SkillCapability {
  adapter_operation?: AdapterOperation
  adapter_tool?: AdapterTool
  confirmation_required?: ConfirmationRequired
  description: Description7
  input_schema: JsonObject
  name: Name11
  output_schema: JsonObject
  required_permissions?: RequiredPermissions
  side_effect?: SideEffect1
  timeout_seconds?: TimeoutSeconds3
  [k: string]: unknown
}
export interface SkillInvocation {
  arguments?: JsonObject
  background?: Background
  capability: Capability2
  skill_id: SkillId2
  [k: string]: unknown
}
export interface SkillResult {
  avatar_cues?: AvatarCues
  data?:
    | string
    | number
    | boolean
    | JsonValue[]
    | {
        [k: string]: JsonValue
      }
    | null
  memory_proposal_ids?: MemoryProposalIds
  prospective_task_ids?: ProspectiveTaskIds
  provenance?: Provenance
  spoken_summary?: SpokenSummary
  status: Status2
  ui_cards?: UiCards
  [k: string]: unknown
}
export interface SkillRunSnapshot {
  capability: Capability3
  completed_at?: CompletedAt2
  confirmation_request_id?: ConfirmationRequestId
  created_at: CreatedAt10
  error?: StructuredError | null
  generation_id?: GenerationId24
  mcp_connection_id?: McpConnectionId1
  origin?: Origin
  plugin_id?: PluginId3
  progress?: Progress
  provider_tool_call_id?: ProviderToolCallId
  result?: SkillResult | null
  session_id: SessionId25
  skill_id: SkillId3
  skill_run_id: SkillRunId14
  skill_version: SkillVersion
  started_at?: StartedAt1
  state: SkillRunState
  turn_id?: TurnId22
  updated_at: UpdatedAt10
  [k: string]: unknown
}
export interface TurnSnapshot {
  active_skill_ids?: ActiveSkillIds
  committed_at?: CommittedAt
  committed_text?: CommittedText
  scene_snapshot_id?: SceneSnapshotId
  session_id: SessionId26
  turn_id: TurnId23
  [k: string]: unknown
}
export interface VideoFrameHeader {
  byte_length: ByteLength1
  codec: Codec1
  end_of_stream?: EndOfStream1
  generation_id?: GenerationId25
  height: Height
  pts_ms: PtsMs1
  sequence: Sequence15
  stream_id: StreamId5
  width: Width
  [k: string]: unknown
}
