import { z } from "zod";

import {
  modelRoleConfigurationSchema,
  type ModelRole,
  type ModelRoleConfiguration,
} from "./contracts";
import { requestRuntime } from "./http";

const configurationsResponseSchema = z.object({
  items: z.array(modelRoleConfigurationSchema),
});
const modelTestResultSchema = z
  .object({
    status: z.string(),
    characters: z.number().int().nonnegative().optional(),
    dimensions: z.number().int().positive().optional(),
  })
  .passthrough();

export async function getModelConfigurations(): Promise<
  ModelRoleConfiguration[]
> {
  return (
    await requestRuntime(
      "/v1/model-configurations",
      configurationsResponseSchema,
    )
  ).items;
}

export async function updateModelConfiguration(
  role: ModelRole,
  configuration: Omit<
    ModelRoleConfiguration,
    "role" | "api_key_configured" | "updated_at"
  > & { api_key?: string; clear_api_key?: boolean },
): Promise<ModelRoleConfiguration> {
  return requestRuntime(
    `/v1/model-configurations/${role}`,
    modelRoleConfigurationSchema,
    { method: "PUT", body: JSON.stringify(configuration) },
  );
}

export async function testModelConfiguration(
  role: ModelRole,
): Promise<z.infer<typeof modelTestResultSchema>> {
  return requestRuntime(
    `/v1/model-configurations/${role}/test`,
    modelTestResultSchema,
    { method: "POST", body: "{}" },
  );
}
