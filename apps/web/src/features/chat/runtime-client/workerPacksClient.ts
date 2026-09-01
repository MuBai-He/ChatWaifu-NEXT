import {
  workerPackIntegrityResponseSchema,
  type WorkerPackIntegrityResponse,
} from "./contracts";
import { requestRuntime } from "./http";

export type { WorkerPackIntegrityResponse } from "./contracts";

const completeVerificationTimeoutMs = 15 * 60 * 1_000;

export function verifyWorkerPackIntegrity(): Promise<WorkerPackIntegrityResponse> {
  return requestRuntime(
    "/v1/worker-packs/integrity/verify",
    workerPackIntegrityResponseSchema,
    {
      method: "POST",
      body: "{}",
      timeoutMs: completeVerificationTimeoutMs,
    },
  );
}
