import {
  parsePluginSnapshot,
  parseSkillDefinition,
  parseSkillRunSnapshot,
  type PluginSnapshot,
  type SkillDefinition,
  type SkillRunSnapshot,
} from "@chatwaifu/protocol";
import { z } from "zod";

import { mutationReceiptSchema, requestRuntime, runtimeParser } from "./http";

const skillConfirmationSchema = z
  .object({
    request_id: z.string().uuid(),
    skill_run_id: z.string().uuid(),
    skill_id: z.string().min(1),
    capability: z.string().min(1),
    permissions: z.array(z.string()),
    side_effect: z.string(),
    reason: z.string(),
    requested_at: z.string().datetime({ offset: true }),
    expires_at: z.string().datetime({ offset: true }),
    allowed_decisions: z.array(
      z.enum(["allow_once", "allow_session", "allow_always", "deny"]),
    ),
    argument_preview: z.object({
      text: z
        .string()
        .max(4096)
        .refine(
          (text) => new TextEncoder().encode(text).byteLength <= 4096,
          "argument preview exceeds 4096 UTF-8 bytes",
        ),
      truncated: z.boolean(),
      redacted: z.boolean(),
    }),
  })
  .passthrough();

export type SkillConfirmation = z.infer<typeof skillConfirmationSchema>;
export type SkillConfirmationDecision =
  SkillConfirmation["allowed_decisions"][number];

function listParser<Result>(parseItem: (input: unknown) => Result) {
  return runtimeParser((input: unknown): Result[] => {
    const payload = z.object({ items: z.array(z.unknown()) }).parse(input);
    return payload.items.map(parseItem);
  });
}

export async function getSkills(): Promise<SkillDefinition[]> {
  return requestRuntime("/v1/skills", listParser(parseSkillDefinition));
}

export async function getSkillInstructions(skillId: string): Promise<string> {
  return (
    await requestRuntime(
      `/v1/skills/${encodeURIComponent(skillId)}/instructions`,
      z.object({ instructions: z.string() }),
    )
  ).instructions;
}

export async function getPlugins(): Promise<PluginSnapshot[]> {
  return requestRuntime("/v1/plugins", listParser(parsePluginSnapshot));
}

export async function installExamplePlugin(): Promise<PluginSnapshot> {
  return requestRuntime(
    "/v1/plugins/install-example",
    runtimeParser(parsePluginSnapshot),
    { method: "POST", body: JSON.stringify({ example_id: "local-echo" }) },
  );
}

export async function installLocalPlugin(
  sourcePath: string,
): Promise<PluginSnapshot> {
  return requestRuntime(
    "/v1/plugins/install",
    runtimeParser(parsePluginSnapshot),
    { method: "POST", body: JSON.stringify({ source_path: sourcePath }) },
  );
}

export async function setPluginEnabled(
  pluginId: string,
  enabled: boolean,
): Promise<PluginSnapshot> {
  return requestRuntime(
    `/v1/plugins/${encodeURIComponent(pluginId)}/enabled`,
    runtimeParser(parsePluginSnapshot),
    { method: "PUT", body: JSON.stringify({ enabled }) },
  );
}

export async function uninstallPlugin(pluginId: string): Promise<void> {
  await requestRuntime(
    `/v1/plugins/${encodeURIComponent(pluginId)}`,
    mutationReceiptSchema,
    { method: "DELETE" },
  );
}

export async function invokeSkill(
  sessionId: string,
  skillId: string,
  capability: string,
  args: Record<string, unknown>,
): Promise<SkillRunSnapshot> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/skill-runs`,
    runtimeParser(parseSkillRunSnapshot),
    {
      method: "POST",
      body: JSON.stringify({
        skill_id: skillId,
        capability,
        arguments: args,
      }),
    },
  );
}

export async function getSkillRuns(
  sessionId: string,
): Promise<SkillRunSnapshot[]> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/skill-runs`,
    listParser(parseSkillRunSnapshot),
  );
}

export async function getSkillConfirmations(
  sessionId: string,
): Promise<SkillConfirmation[]> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/skill-confirmations`,
    runtimeParser((input) => {
      const payload = z.object({ items: z.array(z.unknown()) }).parse(input);
      return payload.items.map((item) => skillConfirmationSchema.parse(item));
    }),
  );
}

export async function decideSkillConfirmation(
  requestId: string,
  decision: "allow_once" | "allow_session" | "allow_always" | "deny",
): Promise<SkillRunSnapshot> {
  return requestRuntime(
    `/v1/skill-confirmations/${requestId}`,
    runtimeParser(parseSkillRunSnapshot),
    { method: "POST", body: JSON.stringify({ decision }) },
  );
}

export async function cancelSkillRun(
  skillRunId: string,
): Promise<SkillRunSnapshot> {
  return requestRuntime(
    `/v1/skill-runs/${skillRunId}/cancel`,
    runtimeParser(parseSkillRunSnapshot),
    { method: "POST", body: "{}" },
  );
}
