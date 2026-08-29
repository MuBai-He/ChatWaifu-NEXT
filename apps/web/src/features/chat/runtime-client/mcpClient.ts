import {
  parseMcpCapabilitySnapshot,
  parseMcpConnectionSnapshot,
  type McpCapabilitySnapshot,
  type McpConnectionConfiguration,
  type McpConnectionSnapshot as ProtocolMcpConnectionSnapshot,
} from "@chatwaifu/protocol";
import { z } from "zod";

import { mutationReceiptSchema, requestRuntime, runtimeParser } from "./http";

export type McpTransport = McpConnectionConfiguration["transport"];
export type McpSandboxMode = NonNullable<
  McpConnectionConfiguration["sandbox_mode"]
>;
export type McpTrustLevel = NonNullable<
  McpConnectionConfiguration["trust_level"]
>;
export type McpNetworkPolicy = NonNullable<
  McpConnectionConfiguration["network_policy"]
>;

export type McpConnectionInput = {
  name: McpConnectionConfiguration["name"];
  transport: McpConnectionConfiguration["transport"];
  command?: string[];
  url?: McpConnectionConfiguration["url"];
  enabled: NonNullable<McpConnectionConfiguration["enabled"]>;
  allow_remote: NonNullable<McpConnectionConfiguration["allow_remote"]>;
  timeout_seconds: NonNullable<McpConnectionConfiguration["timeout_seconds"]>;
  trust_level: McpTrustLevel;
  sandbox_mode: McpSandboxMode;
  network_policy: McpNetworkPolicy;
  bearer_token?: string;
  clear_bearer_token?: boolean;
};

export type McpCapabilitiesSnapshot = McpCapabilitySnapshot &
  Required<
    Pick<
      McpCapabilitySnapshot,
      "tools" | "resources" | "resource_templates" | "prompts"
    >
  >;

export type McpConnectionSnapshot = ProtocolMcpConnectionSnapshot & {
  allow_remote: NonNullable<ProtocolMcpConnectionSnapshot["allow_remote"]>;
  enabled: NonNullable<ProtocolMcpConnectionSnapshot["enabled"]>;
  timeout_seconds: NonNullable<
    ProtocolMcpConnectionSnapshot["timeout_seconds"]
  >;
  trust_level: NonNullable<ProtocolMcpConnectionSnapshot["trust_level"]>;
  sandbox_mode: NonNullable<ProtocolMcpConnectionSnapshot["sandbox_mode"]>;
  network_policy: NonNullable<ProtocolMcpConnectionSnapshot["network_policy"]>;
  status: NonNullable<ProtocolMcpConnectionSnapshot["status"]>;
  bearer_token_configured: NonNullable<
    ProtocolMcpConnectionSnapshot["bearer_token_configured"]
  >;
  capabilities: McpCapabilitiesSnapshot;
};

const connectionParser = runtimeParser(
  (input: unknown): McpConnectionSnapshot =>
    parseMcpConnectionSnapshot(input) as McpConnectionSnapshot,
);
const capabilitiesParser = runtimeParser(
  (input: unknown): McpCapabilitiesSnapshot =>
    parseMcpCapabilitySnapshot(input) as McpCapabilitiesSnapshot,
);

export async function getMcpConnections(): Promise<McpConnectionSnapshot[]> {
  return requestRuntime(
    "/v1/mcp/connections",
    runtimeParser((input) => {
      const payload = z.object({ items: z.array(z.unknown()) }).parse(input);
      return payload.items.map(
        (item) => parseMcpConnectionSnapshot(item) as McpConnectionSnapshot,
      );
    }),
  );
}

export async function createMcpConnection(
  connection: McpConnectionInput,
): Promise<McpConnectionSnapshot> {
  return requestRuntime("/v1/mcp/connections", connectionParser, {
    method: "POST",
    body: JSON.stringify(connection),
  });
}

export async function updateMcpConnection(
  connectionId: string,
  connection: McpConnectionInput,
): Promise<McpConnectionSnapshot> {
  return requestRuntime(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}`,
    connectionParser,
    { method: "PUT", body: JSON.stringify(connection) },
  );
}

export async function deleteMcpConnection(connectionId: string): Promise<void> {
  await requestRuntime(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}`,
    mutationReceiptSchema,
    { method: "DELETE" },
  );
}

export async function testMcpConnection(
  connectionId: string,
): Promise<McpConnectionSnapshot> {
  return requestRuntime(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}/test`,
    connectionParser,
    { method: "POST", body: "{}" },
  );
}

export async function getMcpCapabilities(
  connectionId: string,
): Promise<McpCapabilitiesSnapshot> {
  return requestRuntime(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}/capabilities`,
    capabilitiesParser,
  );
}

export async function readMcpResource(
  sessionId: string,
  connectionId: string,
  uri: string,
): Promise<unknown> {
  return requestRuntime(
    `/v1/sessions/${encodeURIComponent(sessionId)}/mcp/connections/${encodeURIComponent(connectionId)}/resources/read`,
    z.json(),
    { method: "POST", body: JSON.stringify({ uri }) },
  );
}

export async function getMcpPrompt(
  sessionId: string,
  connectionId: string,
  name: string,
  args: Record<string, string>,
): Promise<unknown> {
  return requestRuntime(
    `/v1/sessions/${encodeURIComponent(sessionId)}/mcp/connections/${encodeURIComponent(connectionId)}/prompts/get`,
    z.json(),
    {
      method: "POST",
      body: JSON.stringify({ name, arguments: args }),
    },
  );
}
