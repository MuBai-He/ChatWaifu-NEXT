import { z } from "zod";

import {
  characterProfileSchema,
  runtimeHealthSchema,
  type CharacterProfile,
  type RuntimeHealth,
} from "./contracts";
import { requestRuntime } from "./http";

const charactersResponseSchema = z.object({
  items: z.array(characterProfileSchema),
});

export async function getHealth(signal?: AbortSignal): Promise<RuntimeHealth> {
  return requestRuntime("/v1/runtime/health", runtimeHealthSchema, { signal });
}

export async function getCharacters(
  signal?: AbortSignal,
): Promise<CharacterProfile[]> {
  return (
    await requestRuntime("/v1/characters", charactersResponseSchema, { signal })
  ).items;
}
