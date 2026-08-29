import { z } from "zod";

import {
  aliyunTtsConfigurationSchema,
  ttsConfigurationRegistrationSchema,
  ttsConfigurationSnapshotSchema,
  ttsProviderSnapshotSchema,
  type AliyunCloudTtsConfiguration,
  type AliyunCloudTtsProviderId,
  type TtsProviderSnapshot,
  type TtsConfigurationRegistration,
  type TtsConfigurationSnapshot,
} from "./contracts";
import { requestRuntime } from "./http";

const providersResponseSchema = z.object({
  items: z.array(ttsProviderSnapshotSchema),
});
const configurationsResponseSchema = z.object({
  items: z.array(ttsConfigurationRegistrationSchema),
});
const testResultSchema = z
  .object({
    status: z.string(),
    duration_ms: z.number().int().nonnegative().optional(),
  })
  .passthrough();

export async function getTtsProviders(
  sessionId: string,
): Promise<TtsProviderSnapshot[]> {
  return (
    await requestRuntime(
      `/v1/tts/providers?session_id=${encodeURIComponent(sessionId)}`,
      providersResponseSchema,
    )
  ).items;
}

export async function getTtsConfigurationRegistrations(): Promise<
  TtsConfigurationRegistration[]
> {
  return (
    await requestRuntime("/v1/tts/configurations", configurationsResponseSchema)
  ).items;
}

export async function getTtsConfiguration(
  providerId: string,
): Promise<TtsConfigurationSnapshot> {
  return requestRuntime(
    `/v1/tts/configurations/${encodeURIComponent(providerId)}`,
    ttsConfigurationSnapshotSchema,
  );
}

export async function updateTtsConfiguration(
  providerId: string,
  configuration: Record<string, unknown>,
): Promise<TtsConfigurationSnapshot> {
  const payload = z.record(z.string(), z.json()).parse(configuration);
  return requestRuntime(
    `/v1/tts/configurations/${encodeURIComponent(providerId)}`,
    ttsConfigurationSnapshotSchema,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

export async function testTtsConfiguration(
  providerId: string,
): Promise<z.infer<typeof testResultSchema>> {
  return requestRuntime(
    `/v1/tts/configurations/${encodeURIComponent(providerId)}/test`,
    testResultSchema,
    { method: "POST", body: "{}" },
  );
}

export async function getAliyunTtsConfiguration(
  providerId: AliyunCloudTtsProviderId,
): Promise<AliyunCloudTtsConfiguration> {
  return aliyunTtsConfigurationSchema.parse(
    await getTtsConfiguration(providerId),
  );
}

export async function updateAliyunTtsConfiguration(
  configuration: Omit<
    AliyunCloudTtsConfiguration,
    "provider_id" | "api_key_configured" | "updated_at"
  > & {
    provider_id: AliyunCloudTtsProviderId;
    api_key?: string;
    clear_api_key?: boolean;
  },
): Promise<AliyunCloudTtsConfiguration> {
  const { provider_id: providerId, ...payload } = configuration;
  return aliyunTtsConfigurationSchema.parse(
    await updateTtsConfiguration(providerId, payload),
  );
}

export async function testAliyunTtsConfiguration(
  providerId: AliyunCloudTtsProviderId,
): Promise<z.infer<typeof testResultSchema>> {
  return testTtsConfiguration(providerId);
}
