import {
  realtimeConfigurationSnapshotSchema,
  realtimeConfigurationUpdateSchema,
  type RealtimeConfigurationSnapshot,
  type RealtimeConfigurationUpdate,
} from "./contracts";
import { requestRuntime } from "./http";

export async function getRealtimeConfiguration(
  signal?: AbortSignal,
): Promise<RealtimeConfigurationSnapshot> {
  return requestRuntime(
    "/v1/realtime/configuration",
    realtimeConfigurationSnapshotSchema,
    { signal },
  );
}

export async function updateRealtimeConfiguration(
  update: RealtimeConfigurationUpdate,
  signal?: AbortSignal,
): Promise<RealtimeConfigurationSnapshot> {
  const payload = realtimeConfigurationUpdateSchema.parse(update);
  if (
    payload.clear_api_key &&
    typeof payload.api_key === "string" &&
    payload.api_key.trim().length > 0
  ) {
    throw new Error("不能同时输入新密钥并勾选清除密钥。");
  }

  return requestRuntime(
    "/v1/realtime/configuration",
    realtimeConfigurationSnapshotSchema,
    {
      method: "PUT",
      body: JSON.stringify(payload),
      signal,
    },
  );
}

export function isConflictError(error: unknown): boolean {
  if (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    (error as { status: number }).status === 409
  ) {
    return true;
  }
  if (error instanceof Error) {
    const message = error.message.toLowerCase();
    return (
      message.includes("409") ||
      message.includes("conflict") ||
      message.includes("stale revision") ||
      message.includes("版本冲突")
    );
  }
  return false;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
