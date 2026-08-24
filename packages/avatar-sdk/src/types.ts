import type {
  AvatarCapabilityManifest,
  AvatarCue,
  AvatarInteractionEvent,
} from "@chatwaifu/protocol";

export type AvatarLayer =
  "attention" | "speech" | "emotion" | "gesture" | "gaze" | "override";

export interface Live2DSource {
  modelJsonUrl: string;
  coreScriptUrl?: string;
  bridgeModuleUrl?: string;
  semanticMapping?: Live2DSemanticMapping;
}

export interface Live2DMotionTarget {
  group: string;
  index: number;
}

export interface Live2DSemanticMapping {
  expressions: Record<string, string>;
  motions: Record<string, Live2DMotionTarget>;
}

export interface AvatarHitAreaDefinition {
  id: string;
  semanticTarget: string;
}

export interface AvatarManifest {
  avatarId: string;
  displayName: string;
  rendererKind: "fake" | "live2d";
  capabilities: AvatarCapabilityManifest;
  hitAreas?: AvatarHitAreaDefinition[];
  live2d?: Live2DSource;
}

export interface AvatarWarning {
  code: string;
  message: string;
  cueId?: string;
  action?: string;
}

export interface ScheduledCue {
  cue: AvatarCue;
  layer: AvatarLayer;
  sequence: number;
  startsAtMs: number;
  expiresAtMs: number | null;
}

export interface CueSchedulerSnapshot {
  revision: number;
  active: Partial<Record<AvatarLayer, ScheduledCue>>;
  queued: ScheduledCue[];
  warnings: AvatarWarning[];
}

export interface AvatarRuntimeState {
  revision: number;
  state: string;
  expression: string;
  motion: string | null;
  gaze: string;
  speaking: boolean;
  interrupted: boolean;
  mouthOpen: number;
  activeCues: Partial<Record<AvatarLayer, AvatarCue>>;
}

export interface AvatarHitResult {
  areaId: string;
  semanticTarget: string;
  confidence: number;
  modelX: number;
  modelY: number;
}

export interface AvatarRendererDiagnostics {
  status: "idle" | "loading" | "ready" | "error" | "disposed";
  resourceCount: number;
  contextLosses: number;
  lastError?: AvatarWarning;
}

export interface AvatarTelemetrySnapshot {
  fps: number;
  frameTimeMs: number;
  droppedFrames: number;
  renderedFrames: number;
  contextLosses: number;
  rendererResources: number;
  activeAudioNodes: number;
  heapUsedBytes: number | null;
}

export interface AvatarControllerSnapshot {
  status: "idle" | "loading" | "ready" | "error" | "disposed";
  scheduler: CueSchedulerSnapshot;
  runtime: AvatarRuntimeState;
  telemetry: AvatarTelemetrySnapshot;
  preReadyQueueSize: number;
  warnings: AvatarWarning[];
}

export type AvatarInteractionListener = (event: AvatarInteractionEvent) => void;

export type MotionLifecycleEvent =
  | { type: "motion-started"; cue: ScheduledCue }
  | {
      type: "motion-ended";
      cue: ScheduledCue;
      reason: "expired" | "replaced" | "invalidated";
    };
