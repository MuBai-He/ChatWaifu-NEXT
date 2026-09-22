import { RuntimeRequestError } from "./runtime-client/http";
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

import {
  readConversationScope,
  scopedSessionStorageKey,
} from "./conversationScope";

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
  signal?: AbortSignal,
): Promise<RuntimeSessionBootstrapResult> {
  const [health, characters] = await Promise.all([
    getHealth(signal),
    getCharacters(signal),
  ]);
  signal?.throwIfAborted();
  const character = characters[0];
  if (!character) throw new Error("没有安装角色 manifest。");

  const scope = await readConversationScope(storage);
  const sessionKey = scopedSessionStorageKey(scope);
  const saved = storage.getItem(sessionKey);
  let session = saved
    ? await getSession(saved, signal).catch((error: unknown) => {
        signal?.throwIfAborted();
        if (error instanceof RuntimeRequestError && error.status === 404)
          return null;
        throw error;
      })
    : null;
  signal?.throwIfAborted();
  if (
    !session ||
    session.state !== "ready" ||
    session.participant_id !== scope.participant_id ||
    session.scene_id !== scope.scene_id
  ) {
    session = await createSession(character.character_id, signal, scope);
  }
  signal?.throwIfAborted();
  storage.setItem(sessionKey, session.session_id);
  return { health, character, sessionId: session.session_id };
}

export async function bootstrapChatSession(
  storage: Pick<Storage, "getItem" | "setItem"> = localStorage,
  signal?: AbortSignal,
): Promise<ChatSessionBootstrapResult> {
  const core = await bootstrapRuntimeSession(storage, signal);

  const [recovery, memories, ttsProviders] = await Promise.all([
    getSessionRecovery(core.sessionId, signal),
    getMemory(core.sessionId, signal),
    getTtsProviders(core.sessionId, signal).catch(() => []),
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
