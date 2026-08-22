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
export type Command = SessionStartCommand | TextSendCommand | ConversationInterruptCommand
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
export type GenerationId5 = string
export type InterruptionInitiator = 'user' | 'system' | 'skill'
export type InterruptionId = string
export type Reason1 = string
export type RequestedAt = string
export type SessionId3 = string
export type Code = string
export type Component = string
export type CorrelationId3 = string | null
export type Message = string
export type Retryable = boolean
export type EventModel =
  | SessionCreatedEvent
  | UserTurnCommittedEvent
  | AssistantGenerationStartedEvent
  | AvatarCueEmittedEvent
  | ErrorRaisedEvent
  | GenericCoreEvent
export type CausationId = string | null
export type CorrelationId4 = string | null
export type EventId = string
export type EventType = 'session.created'
export type GenerationId6 = string | null
export type OccurredAt = string
export type CharacterId1 = string
export type PrivacyLevel = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion3 = string
export type Sequence1 = number | null
export type SessionId4 = string | null
export type SkillRunId = string | null
export type Source = string
export type TurnId3 = string | null
export type CausationId1 = string | null
export type CorrelationId5 = string | null
export type EventId1 = string
export type EventType1 = 'user.turn_committed'
export type GenerationId7 = string | null
export type OccurredAt1 = string
export type Text1 = string
export type PrivacyLevel1 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion4 = string
export type Sequence2 = number | null
export type SessionId5 = string | null
export type SkillRunId1 = string | null
export type Source1 = string
export type TurnId4 = string | null
export type CausationId2 = string | null
export type CorrelationId6 = string | null
export type EventId2 = string
export type EventType2 = 'assistant.generation_started'
export type GenerationId8 = string | null
export type OccurredAt2 = string
export type BackendKind = string
export type PrivacyLevel2 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion5 = string
export type Sequence3 = number | null
export type SessionId6 = string | null
export type SkillRunId2 = string | null
export type Source2 = string
export type TurnId5 = string | null
export type CausationId3 = string | null
export type CorrelationId7 = string | null
export type EventId3 = string
export type EventType3 = 'avatar.cue_emitted'
export type GenerationId9 = string | null
export type OccurredAt3 = string
export type PrivacyLevel3 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion6 = string
export type Sequence4 = number | null
export type SessionId7 = string | null
export type SkillRunId3 = string | null
export type Source3 = string
export type TurnId6 = string | null
export type CausationId4 = string | null
export type CorrelationId8 = string | null
export type EventId4 = string
export type EventType4 = 'system.error_raised'
export type GenerationId10 = string | null
export type OccurredAt4 = string
export type PrivacyLevel4 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion7 = string
export type Sequence5 = number | null
export type SessionId8 = string | null
export type SkillRunId4 = string | null
export type Source4 = string
export type TurnId7 = string | null
export type CausationId5 = string | null
export type CorrelationId9 = string | null
export type EventId5 = string
export type GenericCoreEventType =
  | 'system.runtime_started'
  | 'system.runtime_stopping'
  | 'system.component_health_changed'
  | 'session.closed'
  | 'session.state_changed'
  | 'user.speech_started'
  | 'user.speech_progress'
  | 'user.speech_stopped'
  | 'user.transcript_partial'
  | 'user.transcript_final'
  | 'assistant.text_delta'
  | 'assistant.text_segment_committed'
  | 'assistant.generation_cancelled'
  | 'assistant.generation_completed'
  | 'assistant.audio_stream_started'
  | 'assistant.audio_chunk_queued'
  | 'assistant.playback_started'
  | 'assistant.playback_progress'
  | 'assistant.playback_stopped'
  | 'assistant.spoken_text_committed'
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
export type GenerationId11 = string | null
export type OccurredAt5 = string
export type PrivacyLevel5 = 'public' | 'local' | 'private' | 'sensitive'
export type SchemaVersion8 = string
export type Sequence6 = number | null
export type SessionId9 = string | null
export type SkillRunId5 = string | null
export type Source5 = string
export type TurnId8 = string | null
export type BackendKind1 = string
export type CompletedAt = string | null
export type GenerationId12 = string
export type InvalidatedAt = string | null
export type SessionId10 = string
export type StartedAt = string | null
export type GenerationState = 'created' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed'
export type TurnId9 = string
export type Confidence = number
export type CreatedAt = string
export type Importance = number
export type Kind2 = string
export type MemoryId = string
export type Namespace = string
export type ObservedAt = string
export type Predicate = string | null
export type PrivacyLevel6 = 'public' | 'local' | 'private' | 'sensitive'
/**
 * @minItems 1
 */
export type SourceEventIds = [string, ...string[]]
export type State = 'active' | 'superseded' | 'contradicted' | 'tombstoned'
export type SubjectId = string | null
export type Supersedes = string | null
export type Text2 = string
export type UpdatedAt = string
export type ValidFrom = string | null
export type ValidTo = string | null
export type MemoryId1 = string
export type Relevance = number
/**
 * @minItems 1
 */
export type SourceEventIds1 = [string, ...string[]]
export type Text3 = string
export type OpenCommitments = MemoryExcerpt[]
export type PinnedFacts = MemoryExcerpt[]
export type ProvenanceIds = string[]
export type RecentEpisodes = MemoryExcerpt[]
export type RelationshipContext = MemoryExcerpt[]
export type RelevantMemories = MemoryExcerpt[]
export type TokenBudgetUsed = number
export type Confidence1 = number
export type Importance1 = number
export type Kind3 = string
export type Namespace1 = string
export type ObservedAt1 = string
export type Predicate1 = string | null
export type PrivacyLevel7 = 'public' | 'local' | 'private' | 'sensitive'
export type SubjectId1 = string | null
export type Text4 = string
export type Confidence2 = number
/**
 * @minItems 1
 */
export type EvidenceEventIds = [string, ...string[]]
export type Operation = 'add' | 'update' | 'supersede' | 'contradict' | 'forget' | 'ignore'
export type ProposalId = string
export type Rationale = string
export type TargetMemoryId = string | null
export type AdapterVersion = string
export type Capabilities = string[]
export type InputModalities = string[]
export type Kind4 = string
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
export type DecidedAt = string
export type DecidedBy = string
export type Decision = 'allow_once' | 'allow_session' | 'allow_always' | 'deny'
export type Reason2 = string | null
export type RequestId = string
export type Capability = string
export type Permission = string
export type Principal = string
export type Reason3 = string
export type RequestId1 = string
export type RequestedAt1 = string
export type SideEffect = 'read' | 'write' | 'destructive' | 'external_communication' | 'device_control'
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
export type CreatedAt1 = string
export type Revision = number
export type SessionId11 = string
export type SessionState = 'created' | 'connecting' | 'ready' | 'degraded' | 'recovering' | 'closing' | 'closed'
export type UpdatedAt1 = string
export type BackgroundAllowed = boolean
export type ConfirmationRequired = boolean
export type Description = string
export type Name1 = string
export type RequiredPermissions = string[]
export type SideEffect1 = 'read' | 'write' | 'destructive' | 'external_communication' | 'device_control'
export type TimeoutSeconds = number
export type Capabilities1 = SkillCapability[]
export type Description1 = string
export type Interruptible1 = boolean
export type Name2 = string
export type SkillId = string
export type Version = string
export type AvatarCues = AvatarCue[]
export type MemoryProposalIds = string[]
export type ProspectiveTaskIds = string[]
export type Provenance = string[]
export type SpokenSummary = string | null
export type Status = string
export type UiCards = JsonObject[]
export type Progress = number | null
export type SessionId12 = string
export type SkillId1 = string
export type SkillRunId6 = string
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
export type UpdatedAt2 = string
export type ActiveSkillIds = string[]
export type CommittedAt = string | null
export type CommittedText = string | null
export type SceneSnapshotId = string | null
export type SessionId13 = string
export type TurnId10 = string
export type ByteLength1 = number
export type Codec1 = 'jpeg' | 'png' | 'h264' | 'vp8'
export type EndOfStream1 = boolean
export type GenerationId13 = string | null
export type Height = number
export type PtsMs1 = number
export type Sequence7 = number
export type StreamId1 = string
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
  model: ModelManifest
  permission_decision: PermissionDecision
  permission_request: PermissionRequest
  route: RouteDecision
  session: SessionSnapshot
  skill: SkillDefinition
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
export interface ConversationInterruption {
  generation_id: GenerationId5
  initiated_by: InterruptionInitiator
  interruption_id: InterruptionId
  reason: Reason1
  requested_at: RequestedAt
  session_id: SessionId3
  [k: string]: unknown
}
export interface StructuredError {
  code: Code
  component: Component
  correlation_id?: CorrelationId3
  details?: JsonObject
  message: Message
  retryable: Retryable
  [k: string]: unknown
}
export interface SessionCreatedEvent {
  causation_id?: CausationId
  correlation_id?: CorrelationId4
  event_id: EventId
  event_type?: EventType
  generation_id?: GenerationId6
  occurred_at: OccurredAt
  payload: SessionCreatedPayload
  privacy?: PrivacyLevel
  schema_version?: SchemaVersion3
  sequence?: Sequence1
  session_id?: SessionId4
  skill_run_id?: SkillRunId
  source: Source
  turn_id?: TurnId3
  [k: string]: unknown
}
export interface SessionCreatedPayload {
  character_id: CharacterId1
  [k: string]: unknown
}
export interface UserTurnCommittedEvent {
  causation_id?: CausationId1
  correlation_id?: CorrelationId5
  event_id: EventId1
  event_type?: EventType1
  generation_id?: GenerationId7
  occurred_at: OccurredAt1
  payload: UserTurnCommittedPayload
  privacy?: PrivacyLevel1
  schema_version?: SchemaVersion4
  sequence?: Sequence2
  session_id?: SessionId5
  skill_run_id?: SkillRunId1
  source: Source1
  turn_id?: TurnId4
  [k: string]: unknown
}
export interface UserTurnCommittedPayload {
  text: Text1
  [k: string]: unknown
}
export interface AssistantGenerationStartedEvent {
  causation_id?: CausationId2
  correlation_id?: CorrelationId6
  event_id: EventId2
  event_type?: EventType2
  generation_id?: GenerationId8
  occurred_at: OccurredAt2
  payload: AssistantGenerationStartedPayload
  privacy?: PrivacyLevel2
  schema_version?: SchemaVersion5
  sequence?: Sequence3
  session_id?: SessionId6
  skill_run_id?: SkillRunId2
  source: Source2
  turn_id?: TurnId5
  [k: string]: unknown
}
export interface AssistantGenerationStartedPayload {
  backend_kind: BackendKind
  [k: string]: unknown
}
export interface AvatarCueEmittedEvent {
  causation_id?: CausationId3
  correlation_id?: CorrelationId7
  event_id: EventId3
  event_type?: EventType3
  generation_id?: GenerationId9
  occurred_at: OccurredAt3
  payload: AvatarCueEmittedPayload
  privacy?: PrivacyLevel3
  schema_version?: SchemaVersion6
  sequence?: Sequence4
  session_id?: SessionId7
  skill_run_id?: SkillRunId3
  source: Source3
  turn_id?: TurnId6
  [k: string]: unknown
}
export interface AvatarCueEmittedPayload {
  cue: AvatarCue
  [k: string]: unknown
}
export interface ErrorRaisedEvent {
  causation_id?: CausationId4
  correlation_id?: CorrelationId8
  event_id: EventId4
  event_type?: EventType4
  generation_id?: GenerationId10
  occurred_at: OccurredAt4
  payload: ErrorRaisedPayload
  privacy?: PrivacyLevel4
  schema_version?: SchemaVersion7
  sequence?: Sequence5
  session_id?: SessionId8
  skill_run_id?: SkillRunId4
  source: Source4
  turn_id?: TurnId7
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
  causation_id?: CausationId5
  correlation_id?: CorrelationId9
  event_id: EventId5
  event_type: GenericCoreEventType
  generation_id?: GenerationId11
  occurred_at: OccurredAt5
  payload: JsonObject
  privacy?: PrivacyLevel5
  schema_version?: SchemaVersion8
  sequence?: Sequence6
  session_id?: SessionId9
  skill_run_id?: SkillRunId5
  source: Source5
  turn_id?: TurnId8
  [k: string]: unknown
}
export interface GenerationSnapshot {
  backend_kind: BackendKind1
  completed_at?: CompletedAt
  generation_id: GenerationId12
  invalidated_at?: InvalidatedAt
  session_id: SessionId10
  started_at?: StartedAt
  state: GenerationState
  turn_id: TurnId9
  [k: string]: unknown
}
export interface MemoryRecord {
  confidence: Confidence
  created_at: CreatedAt
  importance: Importance
  kind: Kind2
  memory_id: MemoryId
  namespace: Namespace
  observed_at: ObservedAt
  predicate?: Predicate
  sensitivity?: PrivacyLevel6
  source_event_ids: SourceEventIds
  state?: State
  subject_id?: SubjectId
  supersedes?: Supersedes
  text: Text2
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
  memory_id: MemoryId1
  relevance: Relevance
  source_event_ids: SourceEventIds1
  text: Text3
  [k: string]: unknown
}
export interface MemoryProposal {
  candidate?: MemoryRecordDraft | null
  confidence: Confidence2
  evidence_event_ids: EvidenceEventIds
  operation: Operation
  proposal_id: ProposalId
  rationale: Rationale
  target_memory_id?: TargetMemoryId
  [k: string]: unknown
}
export interface MemoryRecordDraft {
  confidence: Confidence1
  importance: Importance1
  kind: Kind3
  namespace: Namespace1
  observed_at: ObservedAt1
  predicate?: Predicate1
  sensitivity?: PrivacyLevel7
  subject_id?: SubjectId1
  text: Text4
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
export interface ModelManifest {
  adapter_version: AdapterVersion
  capabilities?: Capabilities
  input_modalities?: InputModalities
  kind: Kind4
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
  decided_at: DecidedAt
  decided_by: DecidedBy
  decision: Decision
  reason?: Reason2
  request_id: RequestId
  [k: string]: unknown
}
export interface PermissionRequest {
  capability: Capability
  context?: JsonObject
  permission: Permission
  principal: Principal
  reason: Reason3
  request_id: RequestId1
  requested_at: RequestedAt1
  side_effect: SideEffect
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
  created_at: CreatedAt1
  revision: Revision
  session_id: SessionId11
  state: SessionState
  updated_at: UpdatedAt1
  [k: string]: unknown
}
export interface SkillDefinition {
  background_allowed?: BackgroundAllowed
  capabilities?: Capabilities1
  description: Description1
  interruptible?: Interruptible1
  name: Name2
  skill_id: SkillId
  version: Version
  [k: string]: unknown
}
export interface SkillCapability {
  confirmation_required?: ConfirmationRequired
  description: Description
  input_schema: JsonObject
  name: Name1
  output_schema: JsonObject
  required_permissions?: RequiredPermissions
  side_effect?: SideEffect1
  timeout_seconds?: TimeoutSeconds
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
  progress?: Progress
  session_id: SessionId12
  skill_id: SkillId1
  skill_run_id: SkillRunId6
  started_at?: StartedAt1
  state: SkillRunState
  updated_at: UpdatedAt2
  [k: string]: unknown
}
export interface TurnSnapshot {
  active_skill_ids?: ActiveSkillIds
  committed_at?: CommittedAt
  committed_text?: CommittedText
  scene_snapshot_id?: SceneSnapshotId
  session_id: SessionId13
  turn_id: TurnId10
  [k: string]: unknown
}
export interface VideoFrameHeader {
  byte_length: ByteLength1
  codec: Codec1
  end_of_stream?: EndOfStream1
  generation_id?: GenerationId13
  height: Height
  pts_ms: PtsMs1
  sequence: Sequence7
  stream_id: StreamId1
  width: Width
  [k: string]: unknown
}
