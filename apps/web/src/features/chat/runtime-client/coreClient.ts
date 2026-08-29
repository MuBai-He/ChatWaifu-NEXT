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

export async function getHealth(): Promise<RuntimeHealth> {
  return requestRuntime("/v1/runtime/health", runtimeHealthSchema);
}

export async function getCharacters(): Promise<CharacterProfile[]> {
  return (await requestRuntime("/v1/characters", charactersResponseSchema))
    .items;
}
