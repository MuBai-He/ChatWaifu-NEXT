import type { useSettingsRuntime } from "./useSettingsRuntime";

type Runtime = ReturnType<typeof useSettingsRuntime>;

export interface CommonSettingsContext {
  canvasRef: Runtime["canvasRef"];
  appearance: Pick<
    Runtime,
    "avatarManifest" | "snapshot" | "rendererKind" | "character"
  >;
  voice: Pick<
    Runtime,
    | "sessionId"
    | "ttsProviders"
    | "ttsProviderId"
    | "ttsSwitching"
    | "changeTtsProvider"
    | "refreshTtsProviders"
  >;
  data: Pick<Runtime, "sessionId" | "resetting" | "refreshMemories">;
  runtime: Pick<Runtime, "connection" | "health" | "error">;
  sessionId: Runtime["sessionId"];
  resetConversationAndMemory: () => Promise<boolean>;
}

export function settingsContext(runtime: Runtime): CommonSettingsContext {
  return {
    canvasRef: runtime.canvasRef,
    appearance: runtime,
    voice: runtime,
    data: runtime,
    runtime,
    sessionId: runtime.sessionId,
    resetConversationAndMemory: runtime.resetAll,
  };
}
