import { z } from "zod";
import { requestRuntime } from "./runtime-client/http";
import { resolveRuntimeConnection } from "./runtimeEndpoint";

const selectionSchema = z.object({
  participant_id: z.string().min(1),
  scene_id: z.string().nullable(),
});
export type ConversationScope = z.infer<typeof selectionSchema>;
export const OWNER_SCOPE: ConversationScope = {
  participant_id: "local",
  scene_id: null,
};
export const SCOPE_STORAGE_PREFIX = "chatwaifu.next.scope:";
const participantSchema = z.object({
  participant_id: z.string(),
  display_name: z.string(),
});
const sceneSchema = z.object({
  scene_id: z.string(),
  display_name: z.string(),
  participant_ids: z.array(z.string()),
});
export type Participant = z.infer<typeof participantSchema>;
export type Scene = z.infer<typeof sceneSchema>;
export async function scopeStorageKey() {
  return SCOPE_STORAGE_PREFIX + (await resolveRuntimeConnection()).baseUrl;
}
export async function readConversationScope(
  storage: Pick<Storage, "getItem"> = localStorage,
): Promise<ConversationScope> {
  if (!storage) return OWNER_SCOPE;
  const saved = storage.getItem(await scopeStorageKey());
  if (!saved) return OWNER_SCOPE;
  try {
    return selectionSchema.parse(JSON.parse(saved));
  } catch {
    return OWNER_SCOPE;
  }
}
export function scopedSessionStorageKey(scope: ConversationScope) {
  return scope.participant_id === "local" && !scope.scene_id
    ? "chatwaifu.next.session_id"
    : `chatwaifu.next.session_id:${scope.participant_id}:${scope.scene_id ?? "private"}`;
}
export async function getParticipants() {
  return (
    await requestRuntime(
      "/v1/participants",
      z.object({ items: z.array(participantSchema) }),
    )
  ).items;
}
export async function getScenes() {
  return (
    await requestRuntime(
      "/v1/scenes",
      z.object({ items: z.array(sceneSchema) }),
    )
  ).items;
}
export async function createParticipant(display_name: string) {
  return requestRuntime("/v1/participants", participantSchema, {
    method: "POST",
    body: JSON.stringify({ display_name }),
  });
}
export async function createScene(
  display_name: string,
  participant_ids: string[],
) {
  return requestRuntime("/v1/scenes", sceneSchema, {
    method: "POST",
    body: JSON.stringify({ display_name, participant_ids }),
  });
}
