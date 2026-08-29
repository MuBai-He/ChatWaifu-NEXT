import {
  createSession,
  getCharacters,
  getHealth,
  getMemory,
  getSession,
  getSessionRecovery,
  getTtsProviders,
} from "./runtimeClient";
import type {
  CharacterProfile,
  ChatMessage,
  MemoryItem,
  RuntimeHealth,
  TtsProviderSnapshot,
} from "./types";

export const CHAT_SESSION_STORAGE_KEY = "chatwaifu.next.session_id";

export interface ChatSessionBootstrapResult {
  health: RuntimeHealth;
  character: CharacterProfile;
  sessionId: string;
  messages: ChatMessage[];
  memories: MemoryItem[];
  ttsProviders: TtsProviderSnapshot[];
  ttsProviderId: string;
  eventCursor: number;
}

export type RuntimeSessionBootstrapResult = Pick<
  ChatSessionBootstrapResult,
  "health" | "character" | "sessionId"
>;

export async function bootstrapRuntimeSession(
  storage: Pick<Storage, "getItem" | "setItem"> = localStorage,
): Promise<RuntimeSessionBootstrapResult> {
  const [health, characters] = await Promise.all([
    getHealth(),
    getCharacters(),
  ]);
  const character = characters[0];
  if (!character) throw new Error("没有安装角色 manifest。");

  const saved = storage.getItem(CHAT_SESSION_STORAGE_KEY);
  let session = saved ? await getSession(saved).catch(() => null) : null;
  if (!session || session.state !== "ready") {
    session = await createSession(character.character_id);
  }
  storage.setItem(CHAT_SESSION_STORAGE_KEY, session.session_id);
  return { health, character, sessionId: session.session_id };
}

export async function bootstrapChatSession(
  storage: Pick<Storage, "getItem" | "setItem"> = localStorage,
): Promise<ChatSessionBootstrapResult> {
  const core = await bootstrapRuntimeSession(storage);

  const [recovery, memories, ttsProviders] = await Promise.all([
    getSessionRecovery(core.sessionId),
    getMemory(),
    getTtsProviders(core.sessionId).catch(() => []),
  ]);
  return {
    ...core,
    messages: recovery.messages.map((message) => ({
      id: message.turn_id,
      role: message.role,
      text: message.committed_text,
    })),
    memories,
    ttsProviders,
    eventCursor: recovery.after_sequence,
    ttsProviderId:
      ttsProviders.find((provider) => provider.selected)?.provider_id ??
      ttsProviders[0]?.provider_id ??
      "",
  };
}
