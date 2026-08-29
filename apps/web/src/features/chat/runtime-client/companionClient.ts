import {
  companionSettingsSchema,
  companionStatusSchema,
  resourceStatusSchema,
  type CompanionSettings,
  type CompanionStatus,
  type ResourceStatus,
} from "./contracts";
import { requestRuntime } from "./http";

export async function getCompanionStatus(): Promise<CompanionStatus> {
  return requestRuntime("/v1/companion/status", companionStatusSchema);
}

export async function updateCompanionSettings(
  settings: Omit<CompanionSettings, "schema_version" | "updated_at">,
): Promise<CompanionSettings> {
  return requestRuntime("/v1/companion/settings", companionSettingsSchema, {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function sleepCompanionResources(): Promise<ResourceStatus> {
  return requestRuntime("/v1/companion/resources/sleep", resourceStatusSchema, {
    method: "POST",
    body: "{}",
  });
}

export async function wakeCompanionResources(): Promise<ResourceStatus> {
  return requestRuntime("/v1/companion/resources/wake", resourceStatusSchema, {
    method: "POST",
    body: "{}",
  });
}
