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
export type Command = SessionStartCommand | TextSendCommand | ConversationInterruptCommand | PlaybackAckCommand
export type CommandId = string
export type CommandType = 'cmd.session.start'
export type CorrelationId = string | null
export type ExpectedRevision = number | null
export type GenerationId2 = string | null
export type IssuedAt = string
export type Issuer = string
export type CharacterId = string
export type SchemaVersion = string
export type SessionId = string | null
export type TurnId = string | null
export type CommandId1 = string
export type CommandType1 = 'cmd.text.send'
export type CorrelationId1 = string | null
export type ExpectedRevision1 = number | null
export type GenerationId3 = string | null
export type IssuedAt1 = string
export type Issuer1 = string
export type Text = string
export type SchemaVersion1 = string
export type SessionId1 = string | null
export type TurnId1 = string | null
export type CommandId2 = string
export type CommandType2 = 'cmd.conversation.interrupt'
export type CorrelationId2 = string | null
export type ExpectedRevision2 = number | null
export type GenerationId4 = string | null
export type IssuedAt2 = string
export type Issuer2 = string
export type Reason = string
export type SchemaVersion2 = string
export type SessionId2 = string | null
export type TurnId2 = string | null
export type CommandId3 = string
export type CommandType3 = 'cmd.playback.ack'
export type CorrelationId3 = string | null
export type ExpectedRevision3 = number | null
export type GenerationId5 = string | null
export type IssuedAt3 = string
export type Issuer3 = string
export type BufferedMs = number
export type ClientClockMs = number
export type Phase = 'started' | 'progress' | 'stopped' | 'queue_cleared'
export type PlayedPtsMs = number
export type Reason1 = ('ended' | 'interrupted' | 'error' | 'queue_cleared') | null
export type SegmentId = string
export type StreamId1 = string
export type Transport = 'audio_element' | 'webrtc'
export type SchemaVersion3 = string
export type SessionId3 = string | null
export type TurnId3 = string | null
export type GenerationId6 = string
export type InterruptionInitiator = 'user' | 'system' | 'skill'
export type InterruptionId = string
export type Reason2 = string
export type RequestedAt = string
export type SessionId4 = string
export type Code = string
export type Component = string
export type CorrelationId4 = string | null
export type Message = string
export type Retryable = boolean
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
export type GenerationId7 = string | null
export type OccurredAt = string
export type CharacterId1 = string
export type PrivacyLevel = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion4 = string
export type Sequence1 = number | null
export type SessionId5 = string | null
export type SkillRunId = string | null
export type Source = string
export type TurnId4 = string | null
export type CausationId1 = string | null
export type CorrelationId6 = string | null
export type EventId1 = string
export type EventType1 = 'user.turn_committed'
export type GenerationId8 = string | null
export type OccurredAt1 = string
export type Text1 = string
export type PrivacyLevel1 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion5 = string
export type Sequence2 = number | null
export type SessionId6 = string | null
export type SkillRunId1 = string | null
export type Source1 = string
export type TurnId5 = string | null
export type CausationId2 = string | null
export type CorrelationId7 = string | null
export type EventId2 = string
export type EventType2 = 'user.speech_started'
export type GenerationId9 = string | null
export type OccurredAt2 = string
export type AudioStreamId = string
export type Channels1 = number
export type SampleRate1 = number
export type UtteranceId = string
export type PrivacyLevel2 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion6 = string
export type Sequence3 = number | null
export type SessionId7 = string | null
export type SkillRunId2 = string | null
export type Source2 = string
export type TurnId6 = string | null
export type CausationId3 = string | null
export type CorrelationId8 = string | null
export type EventId3 = string
export type EventType3 = 'user.speech_stopped'
export type GenerationId10 = string | null
export type OccurredAt3 = string
export type AudioBytes = number
export type AudioStreamId1 = string
export type DurationMs2 = number
export type UtteranceId1 = string
export type PrivacyLevel3 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion7 = string
export type Sequence4 = number | null
export type SessionId8 = string | null
export type SkillRunId3 = string | null
export type Source3 = string
export type TurnId7 = string | null
export type CausationId4 = string | null
export type CorrelationId9 = string | null
export type EventId4 = string
export type EventType4 = 'user.transcript_partial'
export type GenerationId11 = string | null
export type OccurredAt4 = string
export type IsFinal = boolean
export type Language = string | null
export type Provider = string
export type Text2 = string
export type UtteranceId2 = string
export type PrivacyLevel4 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion8 = string
export type Sequence5 = number | null
export type SessionId9 = string | null
export type SkillRunId4 = string | null
export type Source4 = string
export type TurnId8 = string | null
export type CausationId5 = string | null
export type CorrelationId10 = string | null
export type EventId5 = string
export type EventType5 = 'user.transcript_final'
export type GenerationId12 = string | null
export type OccurredAt5 = string
export type PrivacyLevel5 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion9 = string
export type Sequence6 = number | null
export type SessionId10 = string | null
export type SkillRunId5 = string | null
export type Source5 = string
export type TurnId9 = string | null
export type CausationId6 = string | null
export type CorrelationId11 = string | null
export type EventId6 = string
export type EventType6 = 'assistant.generation_started'
export type GenerationId13 = string | null
export type OccurredAt6 = string
export type BackendKind = string
export type PrivacyLevel6 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion10 = string
export type Sequence7 = number | null
export type SessionId11 = string | null
export type SkillRunId6 = string | null
export type Source6 = string
export type TurnId10 = string | null
export type CausationId7 = string | null
export type CorrelationId12 = string | null
export type EventId7 = string
export type EventType7 = 'assistant.playback_started'
export type GenerationId14 = string | null
export type OccurredAt7 = string
export type BufferedMs1 = number
export type ClientClockMs1 = number
export type PlayedPtsMs1 = number
export type SegmentId1 = string
export type StreamId2 = string
export type Transport1 = 'audio_element' | 'webrtc'
export type PrivacyLevel7 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion11 = string
export type Sequence8 = number | null
export type SessionId12 = string | null
export type SkillRunId7 = string | null
export type Source7 = string
export type TurnId11 = string | null
export type CausationId8 = string | null
export type CorrelationId13 = string | null
export type EventId8 = string
export type EventType8 = 'assistant.playback_progress'
export type GenerationId15 = string | null
export type OccurredAt8 = string
export type PrivacyLevel8 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion12 = string
export type Sequence9 = number | null
export type SessionId13 = string | null
export type SkillRunId8 = string | null
export type Source8 = string
export type TurnId12 = string | null
export type CausationId9 = string | null
export type CorrelationId14 = string | null
export type EventId9 = string
export type EventType9 = 'assistant.playback_stopped'
export type GenerationId16 = string | null
export type OccurredAt9 = string
export type BufferedMs2 = number
export type ClientClockMs2 = number
export type Completed = boolean
export type PlayedPtsMs2 = number
export type Reason3 = 'ended' | 'interrupted' | 'error' | 'queue_cleared'
export type SegmentId2 = string
export type StreamId3 = string
export type Transport2 = 'audio_element' | 'webrtc'
export type PrivacyLevel9 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion13 = string
export type Sequence10 = number | null
export type SessionId14 = string | null
export type SkillRunId9 = string | null
export type Source9 = string
export type TurnId13 = string | null
export type CausationId10 = string | null
export type CorrelationId15 = string | null
export type EventId10 = string
export type EventType10 = 'assistant.spoken_text_committed'
export type GenerationId17 = string | null
export type OccurredAt10 = string
export type SegmentId3 = string
export type SpokenText = string
export type StreamId4 = string
export type Text3 = string
export type PrivacyLevel10 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion14 = string
export type Sequence11 = number | null
export type SessionId15 = string | null
export type SkillRunId10 = string | null
export type Source10 = string
export type TurnId14 = string | null
export type CausationId11 = string | null
export type CorrelationId16 = string | null
export type EventId11 = string
export type EventType11 = 'avatar.cue_emitted'
export type GenerationId18 = string | null
export type OccurredAt11 = string
export type PrivacyLevel11 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion15 = string
export type Sequence12 = number | null
export type SessionId16 = string | null
export type SkillRunId11 = string | null
export type Source11 = string
export type TurnId15 = string | null
export type CausationId12 = string | null
export type CorrelationId17 = string | null
export type EventId12 = string
export type EventType12 = 'system.error_raised'
export type GenerationId19 = string | null
export type OccurredAt12 = string
export type PrivacyLevel12 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion16 = string
export type Sequence13 = number | null
export type SessionId17 = string | null
export type SkillRunId12 = string | null
export type Source12 = string
export type TurnId16 = string | null
export type CausationId13 = string | null
export type CorrelationId18 = string | null
export type EventId13 = string
export type GenericCoreEventType =
  | 'system.runtime_started'
  | 'system.runtime_stopping'
  | 'system.component_health_changed'
  | 'session.closed'
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
  | 'tool.call_started'
  | 'tool.call_completed'
  | 'tool.call_failed'
  | 'memory.proposed'
  | 'memory.committed'
  | 'memory.superseded'
  | 'memory.tombstoned'
  | 'memory.recalled'
  | 'character.state_changed'
  | 'relationship.state_changed'
  | 'avatar.interaction_received'
  | 'model.route_selected'
  | 'model.worker_loaded'
  | 'model.worker_unloaded'
  | 'model.fallback_triggered'
export type GenerationId20 = string | null
export type OccurredAt13 = string
export type PrivacyLevel13 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion17 = string
export type Sequence14 = number | null
export type SessionId18 = string | null
export type SkillRunId13 = string | null
export type Source13 = string
export type TurnId17 = string | null
export type BackendKind1 = string
export type CompletedAt = string | null
export type GenerationId21 = string
export type InvalidatedAt = string | null
export type SessionId19 = string
export type StartedAt = string | null
export type GenerationState = 'created' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed'
export type TurnId18 = string
export type Confidence = number
export type CreatedAt = string
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
export type Text4 = string
export type UpdatedAt = string
export type ValidFrom = string | null
export type ValidTo = string | null
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
export type Text5 = string
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
export type Text6 = string
export type Confidence2 = number
export type CreatedAt1 = string
export type DecidedAt = string | null
/**
 * @minItems 1
 */
export type EvidenceEventIds = [string, ...string[]]
export type MemoryOperation = 'add' | 'update' | 'supersede' | 'contradict' | 'forget' | 'ignore'
export type ProposalId = string
export type Rationale = string
export type TargetMemoryId = string | null
export type CreatedAt2 = string
export type MemoryId2 = string
export type SessionId20 = string
export type SourceEventId = string
export type SourceId = string
export type SourceKind = 'user_turn' | 'memory_management' | 'migration'
export type TurnId19 = string | null
export type AdapterVersion = string
export type Capabilities = string[]
export type InputModalities = string[]
export type Kind2 = string
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
export type Reason4 = string | null
export type RequestId = string
export type Capability = string
export type CreatedAt3 = string
export type ExpiresAt = string | null
export type GrantId = string
export type Permission = string
export type Principal = string
export type RevokedAt = string | null
export type Scope = 'once' | 'session' | 'always'
export type SessionId21 = string | null
export type SkillId = string
export type Capability1 = string
export type Permission1 = string
export type Principal1 = string
export type Reason5 = string
export type RequestId1 = string
export type RequestedAt1 = string
export type SideEffect = 'read' | 'write' | 'destructive' | 'external_communication' | 'device_control'
export type Description = string
export type Enabled = boolean
export type InstallPath = string
export type InstalledAt = string
export type Name1 = string
export type PluginId = string
export type UpdatedAt1 = string
export type Version = string
export type Description1 = string
export type Name2 = string
export type PluginId1 = string
export type SchemaVersion18 = '1.0'
/**
 * @minItems 1
 * @maxItems 32
 */
export type Skills = [string, ...string[]]
/**
 * @minItems 1
 * @maxItems 8
 */
export type Command1 =
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | [string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string]
export type Kind3 = 'stdio'
export type Version1 = string
export type BackendKind2 = string
export type CloudContextPolicy = string
export type EstimatedCost = number | null
export type FallbackChain = string[]
export type ModelId1 = string
export type ProviderId = string
export type ReasonCodes = string[]
export type CharacterId2 = string
export type ConversationState =
  'idle' | 'listening' | 'committing_user_turn' | 'planning' | 'generating' | 'speaking' | 'interrupting' | 'recovering'
export type CreatedAt4 = string
export type Revision = number
export type SessionId22 = string
export type SessionState = 'created' | 'connecting' | 'ready' | 'degraded' | 'recovering' | 'closing' | 'closed'
export type UpdatedAt2 = string
export type BackgroundAllowed = boolean
export type AdapterTool = string | null
export type ConfirmationRequired = boolean
export type Description2 = string
export type Name3 = string
export type RequiredPermissions = string[]
export type SideEffect1 = 'read' | 'write' | 'destructive' | 'external_communication' | 'device_control'
export type TimeoutSeconds = number
export type Capabilities1 = SkillCapability[]
export type Description3 = string
export type Enabled1 = boolean
export type Interruptible1 = boolean
export type Name4 = string
export type PluginId2 = string | null
export type SkillId1 = string
export type Source14 = 'builtin' | 'plugin'
export type Version2 = string
export type Capability2 = string
export type SkillId2 = string
export type AvatarCues = AvatarCue[]
export type MemoryProposalIds = string[]
export type ProspectiveTaskIds = string[]
export type Provenance = string[]
export type SpokenSummary = string | null
export type Status = string
export type UiCards = JsonObject[]
export type Capability3 = string
export type CompletedAt1 = string | null
export type ConfirmationRequestId = string | null
export type CreatedAt5 = string
export type PluginId3 = string | null
export type Progress = number | null
export type SessionId23 = string
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
export type UpdatedAt3 = string
export type ActiveSkillIds = string[]
export type CommittedAt = string | null
export type CommittedText = string | null
export type SceneSnapshotId = string | null
export type SessionId24 = string
export type TurnId20 = string
export type ByteLength1 = number
export type Codec1 = 'jpeg' | 'png' | 'h264' | 'vp8'
export type EndOfStream1 = boolean
export type GenerationId22 = string | null
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
  command: Command
  conversation_interruption: ConversationInterruption
  error: StructuredError
  event: EventModel
  generation: GenerationSnapshot
  memory: MemoryRecord
  memory_context: MemoryContextPacket
  memory_proposal: MemoryProposal
  memory_source: MemorySource
  model: ModelManifest
  permission_decision: PermissionDecision
  permission_grant: PermissionGrant
  permission_request: PermissionRequest
  plugin: PluginSnapshot
  plugin_manifest: PluginManifest
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
export interface SessionStartCommand {
  command_id: CommandId
  command_type?: CommandType
  correlation_id?: CorrelationId
  expected_revision?: ExpectedRevision
  generation_id?: GenerationId2
  issued_at: IssuedAt
  issuer: Issuer
  payload: SessionStartPayload
  schema_version?: SchemaVersion
  session_id?: SessionId
  turn_id?: TurnId
  [k: string]: unknown
}
export interface SessionStartPayload {
  character_id: CharacterId
  [k: string]: unknown
}
export interface TextSendCommand {
  command_id: CommandId1
  command_type?: CommandType1
  correlation_id?: CorrelationId1
  expected_revision?: ExpectedRevision1
  generation_id?: GenerationId3
  issued_at: IssuedAt1
  issuer: Issuer1
  payload: TextSendPayload
  schema_version?: SchemaVersion1
  session_id?: SessionId1
  turn_id?: TurnId1
  [k: string]: unknown
}
export interface TextSendPayload {
  text: Text
  [k: string]: unknown
}
export interface ConversationInterruptCommand {
  command_id: CommandId2
  command_type?: CommandType2
  correlation_id?: CorrelationId2
  expected_revision?: ExpectedRevision2
  generation_id?: GenerationId4
  issued_at: IssuedAt2
  issuer: Issuer2
  payload: ConversationInterruptPayload
  schema_version?: SchemaVersion2
  session_id?: SessionId2
  turn_id?: TurnId2
  [k: string]: unknown
}
export interface ConversationInterruptPayload {
  reason?: Reason
  [k: string]: unknown
}
export interface PlaybackAckCommand {
  command_id: CommandId3
  command_type?: CommandType3
  correlation_id?: CorrelationId3
  expected_revision?: ExpectedRevision3
  generation_id?: GenerationId5
  issued_at: IssuedAt3
  issuer: Issuer3
  payload: PlaybackAckPayload
  schema_version?: SchemaVersion3
  session_id?: SessionId3
  turn_id?: TurnId3
  [k: string]: unknown
}
export interface PlaybackAckPayload {
  buffered_ms: BufferedMs
  client_clock_ms: ClientClockMs
  phase: Phase
  played_pts_ms: PlayedPtsMs
  reason?: Reason1
  segment_id: SegmentId
  stream_id: StreamId1
  transport: Transport
  [k: string]: unknown
}
export interface ConversationInterruption {
  generation_id: GenerationId6
  initiated_by: InterruptionInitiator
  interruption_id: InterruptionId
  reason: Reason2
  requested_at: RequestedAt
  session_id: SessionId4
  [k: string]: unknown
}
export interface StructuredError {
  code: Code
  component: Component
  correlation_id?: CorrelationId4
  details?: JsonObject
  message: Message
  retryable: Retryable
  [k: string]: unknown
}
export interface SessionCreatedEvent {
  causation_id?: CausationId
  correlation_id?: CorrelationId5
  event_id: EventId
  event_type?: EventType
  generation_id?: GenerationId7
  occurred_at: OccurredAt
  payload: SessionCreatedPayload
  privacy?: PrivacyLevel
  schema_version?: SchemaVersion4
  sequence?: Sequence1
  session_id?: SessionId5
  skill_run_id?: SkillRunId
  source: Source
  turn_id?: TurnId4
  [k: string]: unknown
}
export interface SessionCreatedPayload {
  character_id: CharacterId1
  [k: string]: unknown
}
export interface UserTurnCommittedEvent {
  causation_id?: CausationId1
  correlation_id?: CorrelationId6
  event_id: EventId1
  event_type?: EventType1
  generation_id?: GenerationId8
  occurred_at: OccurredAt1
  payload: UserTurnCommittedPayload
  privacy?: PrivacyLevel1
  schema_version?: SchemaVersion5
  sequence?: Sequence2
  session_id?: SessionId6
  skill_run_id?: SkillRunId1
  source: Source1
  turn_id?: TurnId5
  [k: string]: unknown
}
export interface UserTurnCommittedPayload {
  text: Text1
  [k: string]: unknown
}
export interface UserSpeechStartedEvent {
  causation_id?: CausationId2
  correlation_id?: CorrelationId7
  event_id: EventId2
  event_type?: EventType2
  generation_id?: GenerationId9
  occurred_at: OccurredAt2
  payload: UserSpeechStartedPayload
  privacy?: PrivacyLevel2
  schema_version?: SchemaVersion6
  sequence?: Sequence3
  session_id?: SessionId7
  skill_run_id?: SkillRunId2
  source: Source2
  turn_id?: TurnId6
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
  generation_id?: GenerationId10
  occurred_at: OccurredAt3
  payload: UserSpeechStoppedPayload
  privacy?: PrivacyLevel3
  schema_version?: SchemaVersion7
  sequence?: Sequence4
  session_id?: SessionId8
  skill_run_id?: SkillRunId3
  source: Source3
  turn_id?: TurnId7
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
  generation_id?: GenerationId11
  occurred_at: OccurredAt4
  payload: UserTranscriptPayload
  privacy?: PrivacyLevel4
  schema_version?: SchemaVersion8
  sequence?: Sequence5
  session_id?: SessionId9
  skill_run_id?: SkillRunId4
  source: Source4
  turn_id?: TurnId8
  [k: string]: unknown
}
export interface UserTranscriptPayload {
  is_final: IsFinal
  language?: Language
  provider: Provider
  text: Text2
  utterance_id: UtteranceId2
  [k: string]: unknown
}
export interface UserTranscriptFinalEvent {
  causation_id?: CausationId5
  correlation_id?: CorrelationId10
  event_id: EventId5
  event_type?: EventType5
  generation_id?: GenerationId12
  occurred_at: OccurredAt5
  payload: UserTranscriptPayload
  privacy?: PrivacyLevel5
  schema_version?: SchemaVersion9
  sequence?: Sequence6
  session_id?: SessionId10
  skill_run_id?: SkillRunId5
  source: Source5
  turn_id?: TurnId9
  [k: string]: unknown
}
export interface AssistantGenerationStartedEvent {
  causation_id?: CausationId6
  correlation_id?: CorrelationId11
  event_id: EventId6
  event_type?: EventType6
  generation_id?: GenerationId13
  occurred_at: OccurredAt6
  payload: AssistantGenerationStartedPayload
  privacy?: PrivacyLevel6
  schema_version?: SchemaVersion10
  sequence?: Sequence7
  session_id?: SessionId11
  skill_run_id?: SkillRunId6
  source: Source6
  turn_id?: TurnId10
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
  generation_id?: GenerationId14
  occurred_at: OccurredAt7
  payload: AssistantPlaybackPayload
  privacy?: PrivacyLevel7
  schema_version?: SchemaVersion11
  sequence?: Sequence8
  session_id?: SessionId12
  skill_run_id?: SkillRunId7
  source: Source7
  turn_id?: TurnId11
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
  generation_id?: GenerationId15
  occurred_at: OccurredAt8
  payload: AssistantPlaybackPayload
  privacy?: PrivacyLevel8
  schema_version?: SchemaVersion12
  sequence?: Sequence9
  session_id?: SessionId13
  skill_run_id?: SkillRunId8
  source: Source8
  turn_id?: TurnId12
  [k: string]: unknown
}
export interface AssistantPlaybackStoppedEvent {
  causation_id?: CausationId9
  correlation_id?: CorrelationId14
  event_id: EventId9
  event_type?: EventType9
  generation_id?: GenerationId16
  occurred_at: OccurredAt9
  payload: AssistantPlaybackStoppedPayload
  privacy?: PrivacyLevel9
  schema_version?: SchemaVersion13
  sequence?: Sequence10
  session_id?: SessionId14
  skill_run_id?: SkillRunId9
  source: Source9
  turn_id?: TurnId13
  [k: string]: unknown
}
export interface AssistantPlaybackStoppedPayload {
  buffered_ms: BufferedMs2
  client_clock_ms: ClientClockMs2
  completed: Completed
  played_pts_ms: PlayedPtsMs2
  reason: Reason3
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
  generation_id?: GenerationId17
  occurred_at: OccurredAt10
  payload: AssistantSpokenTextCommittedPayload
  privacy?: PrivacyLevel10
  schema_version?: SchemaVersion14
  sequence?: Sequence11
  session_id?: SessionId15
  skill_run_id?: SkillRunId10
  source: Source10
  turn_id?: TurnId14
  [k: string]: unknown
}
export interface AssistantSpokenTextCommittedPayload {
  segment_id: SegmentId3
  spoken_text: SpokenText
  stream_id: StreamId4
  text: Text3
  [k: string]: unknown
}
export interface AvatarCueEmittedEvent {
  causation_id?: CausationId11
  correlation_id?: CorrelationId16
  event_id: EventId11
  event_type?: EventType11
  generation_id?: GenerationId18
  occurred_at: OccurredAt11
  payload: AvatarCueEmittedPayload
  privacy?: PrivacyLevel11
  schema_version?: SchemaVersion15
  sequence?: Sequence12
  session_id?: SessionId16
  skill_run_id?: SkillRunId11
  source: Source11
  turn_id?: TurnId15
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
  generation_id?: GenerationId19
  occurred_at: OccurredAt12
  payload: ErrorRaisedPayload
  privacy?: PrivacyLevel12
  schema_version?: SchemaVersion16
  sequence?: Sequence13
  session_id?: SessionId17
  skill_run_id?: SkillRunId12
  source: Source12
  turn_id?: TurnId16
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
  generation_id?: GenerationId20
  occurred_at: OccurredAt13
  payload: JsonObject
  privacy?: PrivacyLevel13
  schema_version?: SchemaVersion17
  sequence?: Sequence14
  session_id?: SessionId18
  skill_run_id?: SkillRunId13
  source: Source13
  turn_id?: TurnId17
  [k: string]: unknown
}
export interface GenerationSnapshot {
  backend_kind: BackendKind1
  completed_at?: CompletedAt
  generation_id: GenerationId21
  invalidated_at?: InvalidatedAt
  session_id: SessionId19
  started_at?: StartedAt
  state: GenerationState
  turn_id: TurnId18
  [k: string]: unknown
}
export interface MemoryRecord {
  confidence: Confidence
  created_at: CreatedAt
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
  text: Text4
  updated_at: UpdatedAt
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
  entity_relevance?: EntityRelevance
  lexical_relevance?: LexicalRelevance
  memory_id: MemoryId1
  relevance: Relevance
  retrieval_sources?: RetrievalSources
  semantic_relevance?: SemanticRelevance
  source_event_ids: SourceEventIds1
  temporal_relevance?: TemporalRelevance
  text: Text5
  [k: string]: unknown
}
export interface MemoryProposal {
  candidate?: MemoryRecordDraft | null
  confidence: Confidence2
  created_at: CreatedAt1
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
  text: Text6
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
  created_at: CreatedAt2
  memory_id: MemoryId2
  session_id: SessionId20
  source_event_id: SourceEventId
  source_id: SourceId
  source_kind: SourceKind
  turn_id?: TurnId19
  [k: string]: unknown
}
export interface ModelManifest {
  adapter_version: AdapterVersion
  capabilities?: Capabilities
  input_modalities?: InputModalities
  kind: Kind2
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
  reason?: Reason4
  request_id: RequestId
  [k: string]: unknown
}
export interface PermissionGrant {
  capability: Capability
  created_at: CreatedAt3
  expires_at?: ExpiresAt
  grant_id: GrantId
  permission: Permission
  principal: Principal
  revoked_at?: RevokedAt
  scope: Scope
  session_id?: SessionId21
  skill_id: SkillId
  [k: string]: unknown
}
export interface PermissionRequest {
  capability: Capability1
  context?: JsonObject
  permission: Permission1
  principal: Principal1
  reason: Reason5
  request_id: RequestId1
  requested_at: RequestedAt1
  side_effect: SideEffect
  [k: string]: unknown
}
export interface PluginSnapshot {
  description: Description
  enabled: Enabled
  install_path: InstallPath
  installed_at: InstalledAt
  name: Name1
  plugin_id: PluginId
  updated_at: UpdatedAt1
  version: Version
  [k: string]: unknown
}
export interface PluginManifest {
  description: Description1
  name: Name2
  plugin_id: PluginId1
  schema_version?: SchemaVersion18
  skills: Skills
  transport: PluginTransport
  version: Version1
  [k: string]: unknown
}
export interface PluginTransport {
  command: Command1
  kind?: Kind3
  [k: string]: unknown
}
export interface RouteDecision {
  backend_kind: BackendKind2
  cloud_context_policy: CloudContextPolicy
  estimated_cost?: EstimatedCost
  fallback_chain?: FallbackChain
  model_id: ModelId1
  provider_id: ProviderId
  reason_codes?: ReasonCodes
  [k: string]: unknown
}
export interface SessionSnapshot {
  character_id: CharacterId2
  conversation_state: ConversationState
  created_at: CreatedAt4
  revision: Revision
  session_id: SessionId22
  state: SessionState
  updated_at: UpdatedAt2
  [k: string]: unknown
}
export interface SkillDefinition {
  background_allowed?: BackgroundAllowed
  capabilities?: Capabilities1
  description: Description3
  enabled?: Enabled1
  interruptible?: Interruptible1
  name: Name4
  plugin_id?: PluginId2
  skill_id: SkillId1
  source?: Source14
  version: Version2
  [k: string]: unknown
}
export interface SkillCapability {
  adapter_tool?: AdapterTool
  confirmation_required?: ConfirmationRequired
  description: Description2
  input_schema: JsonObject
  name: Name3
  output_schema: JsonObject
  required_permissions?: RequiredPermissions
  side_effect?: SideEffect1
  timeout_seconds?: TimeoutSeconds
  [k: string]: unknown
}
export interface SkillInvocation {
  arguments?: JsonObject
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
  status: Status
  ui_cards?: UiCards
  [k: string]: unknown
}
export interface SkillRunSnapshot {
  capability: Capability3
  completed_at?: CompletedAt1
  confirmation_request_id?: ConfirmationRequestId
  created_at: CreatedAt5
  error?: StructuredError | null
  plugin_id?: PluginId3
  progress?: Progress
  result?: SkillResult | null
  session_id: SessionId23
  skill_id: SkillId3
  skill_run_id: SkillRunId14
  skill_version: SkillVersion
  started_at?: StartedAt1
  state: SkillRunState
  updated_at: UpdatedAt3
  [k: string]: unknown
}
export interface TurnSnapshot {
  active_skill_ids?: ActiveSkillIds
  committed_at?: CommittedAt
  committed_text?: CommittedText
  scene_snapshot_id?: SceneSnapshotId
  session_id: SessionId24
  turn_id: TurnId20
  [k: string]: unknown
}
export interface VideoFrameHeader {
  byte_length: ByteLength1
  codec: Codec1
  end_of_stream?: EndOfStream1
  generation_id?: GenerationId22
  height: Height
  pts_ms: PtsMs1
  sequence: Sequence15
  stream_id: StreamId5
  width: Width
  [k: string]: unknown
}
