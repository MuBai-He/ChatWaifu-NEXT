import type { useDesktopPreferences } from "../desktop-pet/useDesktopPreferences";
import type { useSettingsRuntime } from "./useSettingsRuntime";

type SettingsRuntimeState = ReturnType<typeof useSettingsRuntime>;

export interface SettingsRuntimeContext {
  canvasRef: SettingsRuntimeState["canvasRef"];
  appearance: Pick<
    SettingsRuntimeState,
    "snapshot" | "rendererKind" | "character"
  >;
  voice: Pick<
    SettingsRuntimeState,
    | "sessionId"
    | "ttsProviders"
    | "ttsProviderId"
    | "ttsSwitching"
    | "changeTtsProvider"
    | "refreshTtsProviders"
  >;
  data: Pick<
    SettingsRuntimeState,
    "sessionId" | "resetting" | "refreshMemories"
  >;
  runtime: Pick<SettingsRuntimeState, "connection" | "health" | "error">;
  sessionId: SettingsRuntimeState["sessionId"];
  desktop: ReturnType<typeof useDesktopPreferences>;
  resetConversationAndMemory: () => Promise<void>;
}

// Compatibility name for existing section components. The context itself is
// now intentionally sourced from useSettingsRuntime, never useChatSession.
export type DesktopSettingsContext = SettingsRuntimeContext;
