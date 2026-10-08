import {
  parseAgentTask,
  parseChannelDeliveryPlanSnapshot,
  parseDecisionRecord,
  parseAgentTaskPage,
  parseArtifactRef,
  parseCapabilityPage,
  parseGroupAutonomyPolicy,
  type AgentTaskCreate,
  type ArtifactRef,
  type GroupAutonomyPolicy,
  parseCandidateFeature,
  parseDevelopmentPolicy,
  type AgentDevelopmentPolicy,
  type CandidateCreate,
  type TaskAuthorization,
} from "@chatwaifu/protocol";
import { z } from "zod";
import { requestRuntime, runtimeParser } from "./http";
import {
  readRuntimeRequestContext,
  assertRuntimeRequestContext,
} from "../runtimeEndpoint";

export const getAgentCapabilities = (
  sessionId: string,
  query = "",
  cursor?: string,
) =>
  requestRuntime(
    `/v1/agent/capabilities?${new URLSearchParams({
      session_id: sessionId,
      query,
      ...(cursor ? { cursor } : {}),
    })}`,
    runtimeParser(parseCapabilityPage),
  );
export const getAgentTasks = (sessionId: string, cursor?: string) =>
  requestRuntime(
    `/v1/agent/tasks?${new URLSearchParams({
      session_id: sessionId,
      ...(cursor ? { cursor } : {}),
    })}`,
    runtimeParser(parseAgentTaskPage),
  );
export const createAgentTask = (body: AgentTaskCreate) =>
  requestRuntime("/v1/agent/tasks", runtimeParser(parseAgentTask), {
    method: "POST",
    body: JSON.stringify(body),
  });
export const actOnAgentTask = (
  sessionId: string,
  taskId: string,
  revision: number,
  action: "pause" | "resume" | "cancel" | "defer",
  inputText?: string,
  wakeAt?: string,
) =>
  requestRuntime(
    `/v1/agent/tasks/${taskId}/actions?session_id=${sessionId}`,
    runtimeParser(parseAgentTask),
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: revision,
        action,
        input_text: inputText,
        wake_at: wakeAt,
      }),
    },
  );
export const getAgentArtifacts = (sessionId: string) =>
  requestRuntime(
    `/v1/agent/artifacts?session_id=${sessionId}`,
    runtimeParser((value) =>
      z.array(z.unknown()).max(100).parse(value).map(parseArtifactRef),
    ),
  );
export const getAgentJournal = (sessionId: string, taskId: string) =>
  requestRuntime(
    `/v1/agent/tasks/${taskId}/journal?session_id=${sessionId}`,
    z
      .object({
        schema_version: z.literal("1.0"),
        items: z.array(z.record(z.string(), z.unknown())).max(100),
      })
      .strict(),
  );
export const getAgentDelivery = (sessionId: string, taskId: string) =>
  requestRuntime(
    `/v1/agent/tasks/${taskId}/delivery?session_id=${sessionId}`,
    runtimeParser((value) =>
      value === null ? null : parseChannelDeliveryPlanSnapshot(value),
    ),
  );
export const updateTaskAuthorization = (
  sessionId: string,
  taskId: string,
  revision: number,
  authorization: TaskAuthorization,
) =>
  requestRuntime(
    `/v1/agent/tasks/${taskId}/authorization?session_id=${sessionId}`,
    runtimeParser(parseAgentTask),
    {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: revision,
        authorization,
      }),
    },
  );
export const reconcileAgentOperation = (
  sessionId: string,
  taskId: string,
  revision: number,
  stepKey: string,
  outcome: "accepted" | "rejected",
  evidenceRef: string,
) =>
  requestRuntime(
    `/v1/agent/tasks/${taskId}/reconcile?session_id=${sessionId}`,
    runtimeParser(parseAgentTask),
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: revision,
        step_key: stepKey,
        outcome,
        evidence_ref: evidenceRef,
      }),
    },
  );
export const getGroupAutonomy = (routeId: string) =>
  requestRuntime(
    `/v1/agent/groups/${routeId}/policy`,
    runtimeParser(parseGroupAutonomyPolicy),
  );
export const saveGroupAutonomy = (policy: GroupAutonomyPolicy) =>
  requestRuntime(
    `/v1/agent/groups/${policy.route_id}/policy`,
    runtimeParser(parseGroupAutonomyPolicy),
    {
      method: "PUT",
      body: JSON.stringify({ expected_revision: policy.revision, policy }),
    },
  );

export const getAgentDevelopment = () =>
  requestRuntime(
    "/v1/agent/development",
    runtimeParser(parseDevelopmentPolicy),
  );
export const configureAgentDevelopment = (policy: AgentDevelopmentPolicy) =>
  requestRuntime(
    "/v1/agent/development",
    runtimeParser(parseDevelopmentPolicy),
    {
      method: "PUT",
      body: JSON.stringify(policy),
    },
  );
export const getAgentCandidates = (sessionId: string) =>
  requestRuntime(
    `/v1/agent/candidates?session_id=${sessionId}`,
    runtimeParser((v) =>
      z.array(z.unknown()).max(100).parse(v).map(parseCandidateFeature),
    ),
  );
export const createAgentCandidate = (body: CandidateCreate) =>
  requestRuntime("/v1/agent/candidates", runtimeParser(parseCandidateFeature), {
    method: "POST",
    body: JSON.stringify(body),
  });
export const approveAgentCandidate = (
  candidateId: string,
  revision: number,
  sha256: string,
) =>
  requestRuntime(
    `/v1/agent/candidates/${candidateId}/approve`,
    runtimeParser(parseCandidateFeature),
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: revision,
        package_sha256: sha256,
      }),
    },
  );

export async function readAgentArtifactBlob(
  sessionId: string,
  artifact: ArtifactRef,
): Promise<Blob> {
  const context = await readRuntimeRequestContext();
  const connection = context.connection;
  const response = await fetch(
    `${connection.baseUrl}/v1/agent/artifacts/${artifact.artifact_id}/content?session_id=${sessionId}`,
    {
      headers: connection.token
        ? { Authorization: `Bearer ${connection.token}` }
        : {},
      signal: AbortSignal.timeout(30000),
    },
  );
  assertRuntimeRequestContext(context);
  if (!response.ok) throw new Error("产物访问失败，请刷新后重试");
  const blob = await response.blob();
  if (blob.size !== artifact.byte_length) throw new Error("产物大小校验失败");
  const digest = Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()),
    ),
  )
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
  if (digest !== artifact.sha256) throw new Error("产物校验值不一致");
  assertRuntimeRequestContext(context);
  return blob;
}

export async function downloadAgentArtifact(
  sessionId: string,
  artifact: ArtifactRef,
): Promise<void> {
  const blob = await readAgentArtifactBlob(sessionId, artifact);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = artifact.name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const getGroupDecisions = (routeId: string) =>
  requestRuntime(
    `/v1/agent/groups/${routeId}/decisions`,
    runtimeParser((value) =>
      z.array(z.unknown()).max(30).parse(value).map(parseDecisionRecord),
    ),
  );
