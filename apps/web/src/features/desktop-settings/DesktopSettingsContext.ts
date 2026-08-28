import type { useChatSession } from "../chat/useChatSession";
import type { useDesktopPreferences } from "../desktop-pet/useDesktopPreferences";

type ChatSettingsState = ReturnType<typeof useChatSession>;

export interface DesktopSettingsContext {
  canvasRef: ChatSettingsState["canvasRef"];
  appearance: Pick<
    ChatSettingsState,
    "snapshot" | "rendererKind" | "character"
  >;
  voice: Pick<
    ChatSettingsState,
    | "sessionId"
    | "ttsProviders"
    | "ttsProviderId"
    | "ttsSwitching"
    | "changeTtsProvider"
    | "refreshTtsProviders"
  >;
  data: Pick<ChatSettingsState, "sessionId" | "resetting" | "refreshMemories">;
  runtime: Pick<ChatSettingsState, "connection" | "health" | "error">;
  sessionId: ChatSettingsState["sessionId"];
  desktop: ReturnType<typeof useDesktopPreferences>;
  resetConversationAndMemory: () => Promise<void>;
}
