/**
 * Internal testing registry and recursive enum path collectors for contract parity testing.
 * Not exported from the root `@chatwaifu/protocol` package.
 */

import { z } from "zod";
import {
  audioFrameHeaderSchema,
  avatarCapabilityManifestSchema,
  avatarCueSchema,
  avatarInteractionEventSchema,
  channelAuthorizationSnapshotSchema,
  channelAuthorizationStartRequestSchema,
  channelAuthorizationVerificationRequestSchema,
  channelConnectionConfigurationSchema,
  channelConnectionSnapshotSchema,
  channelDeliveryAcknowledgementSchema,
  channelDeliveryClaimRequestSchema,
  channelDeliveryPartAcknowledgementSchema,
  channelDeliveryPartClaimRequestSchema,
  channelDeliveryPartKindSchema,
  channelDeliveryPartPayloadSchema,
  channelDeliveryPartSnapshotSchema,
  channelDeliveryPartStatusSchema,
  channelDeliveryPlanSnapshotSchema,
  channelDeliverySnapshotSchema,
  channelDeliveryStatusSchema,
  channelErrorResponseSchema,
  channelGatewayStatusSnapshotSchema,
  channelInboundTextMessageSchema,
  channelProviderCapabilitiesSchema,
  channelProviderRegistrationSchema,
  channelTextDeliveryPartPayloadSchema,
  channelTurnCancelReceiptSchema,
  channelTurnCancelRequestSchema,
  channelTurnReceiptSchema,
  channelTurnSnapshotSchema,
  channelTurnStatusSchema,
  characterKernelSnapshotSchema,
  commandEnvelopeSchema,
  eventEnvelopeSchema,
  genericCoreEventSchema,
  mcpCapabilitySnapshotSchema,
  mcpConnectionSnapshotSchema,
  mcpPromptSchema,
  mcpResourceSchema,
  mcpResourceTemplateSchema,
  mcpToolSchema,
  memoryChannelAttributionSchema,
  memoryDraftSchema,
  memoryProposalSchema,
  memoryRecordSchema,
  memorySourceSchema,
  pluginSnapshotSchema,
  sessionSnapshotSchema,
  skillCapabilitySchema,
  skillDefinitionSchema,
  skillResultSchema,
  skillRunSnapshotSchema,
  strongEventEnvelopeSchema,
  structuredErrorSchema,
} from "../parsers/protocol";

export {
  audioFrameHeaderSchema,
  avatarCapabilityManifestSchema,
  avatarCueSchema,
  avatarInteractionEventSchema,
  channelAuthorizationSnapshotSchema,
  channelAuthorizationStartRequestSchema,
  channelAuthorizationVerificationRequestSchema,
  channelConnectionConfigurationSchema,
  channelConnectionSnapshotSchema,
  channelDeliveryAcknowledgementSchema,
  channelDeliveryClaimRequestSchema,
  channelDeliveryPartAcknowledgementSchema,
  channelDeliveryPartClaimRequestSchema,
  channelDeliveryPartKindSchema,
  channelDeliveryPartPayloadSchema,
  channelDeliveryPartSnapshotSchema,
  channelDeliveryPartStatusSchema,
  channelDeliveryPlanSnapshotSchema,
  channelDeliverySnapshotSchema,
  channelDeliveryStatusSchema,
  channelErrorResponseSchema,
  channelGatewayStatusSnapshotSchema,
  channelInboundTextMessageSchema,
  channelProviderCapabilitiesSchema,
  channelProviderRegistrationSchema,
  channelTextDeliveryPartPayloadSchema,
  channelTurnCancelReceiptSchema,
  channelTurnCancelRequestSchema,
  channelTurnReceiptSchema,
  channelTurnSnapshotSchema,
  channelTurnStatusSchema,
  characterKernelSnapshotSchema,
  commandEnvelopeSchema,
  eventEnvelopeSchema,
  genericCoreEventSchema,
  mcpCapabilitySnapshotSchema,
  mcpConnectionSnapshotSchema,
  mcpPromptSchema,
  mcpResourceSchema,
  mcpResourceTemplateSchema,
  mcpToolSchema,
  memoryChannelAttributionSchema,
  memoryDraftSchema,
  memoryProposalSchema,
  memoryRecordSchema,
  memorySourceSchema,
  pluginSnapshotSchema,
  sessionSnapshotSchema,
  skillCapabilitySchema,
  skillDefinitionSchema,
  skillResultSchema,
  skillRunSnapshotSchema,
  strongEventEnvelopeSchema,
  structuredErrorSchema,
};

export type PublicParserDescriptor = {
  modelName: string;
  schema: z.ZodTypeAny;
};

/**
 * Maps every public parseXxx function to its catalog definition model name and Zod schema.
 */
export const publicParserCatalogMapping: Record<
  string,
  PublicParserDescriptor
> = {
  parseAudioFrameHeader: {
    modelName: "AudioFrameHeader",
    schema: audioFrameHeaderSchema,
  },
  parseAvatarCue: {
    modelName: "AvatarCue",
    schema: avatarCueSchema,
  },
  parseAvatarCapabilityManifest: {
    modelName: "AvatarCapabilityManifest",
    schema: avatarCapabilityManifestSchema,
  },
  parseAvatarInteractionEvent: {
    modelName: "AvatarInteractionEvent",
    schema: avatarInteractionEventSchema,
  },
  parseSessionSnapshot: {
    modelName: "SessionSnapshot",
    schema: sessionSnapshotSchema,
  },
  parseCharacterKernelSnapshot: {
    modelName: "CharacterKernelSnapshot",
    schema: characterKernelSnapshotSchema,
  },
  parseChannelProviderRegistration: {
    modelName: "ChannelProviderRegistration",
    schema: channelProviderRegistrationSchema,
  },
  parseChannelAuthorizationStartRequest: {
    modelName: "ChannelAuthorizationStartRequest",
    schema: channelAuthorizationStartRequestSchema,
  },
  parseChannelAuthorizationVerificationRequest: {
    modelName: "ChannelAuthorizationVerificationRequest",
    schema: channelAuthorizationVerificationRequestSchema,
  },
  parseChannelAuthorizationSnapshot: {
    modelName: "ChannelAuthorizationSnapshot",
    schema: channelAuthorizationSnapshotSchema,
  },
  parseChannelConnectionConfiguration: {
    modelName: "ChannelConnectionConfiguration",
    schema: channelConnectionConfigurationSchema,
  },
  parseChannelConnectionSnapshot: {
    modelName: "ChannelConnectionSnapshot",
    schema: channelConnectionSnapshotSchema,
  },
  parseChannelGatewayStatusSnapshot: {
    modelName: "ChannelGatewayStatusSnapshot",
    schema: channelGatewayStatusSnapshotSchema,
  },
  parseChannelInboundTextMessage: {
    modelName: "ChannelInboundTextMessage",
    schema: channelInboundTextMessageSchema,
  },
  parseChannelTurnReceipt: {
    modelName: "ChannelTurnReceipt",
    schema: channelTurnReceiptSchema,
  },
  parseChannelTurnSnapshot: {
    modelName: "ChannelTurnSnapshot",
    schema: channelTurnSnapshotSchema,
  },
  parseChannelDeliveryAcknowledgement: {
    modelName: "ChannelDeliveryAcknowledgement",
    schema: channelDeliveryAcknowledgementSchema,
  },
  parseChannelDeliveryClaimRequest: {
    modelName: "ChannelDeliveryClaimRequest",
    schema: channelDeliveryClaimRequestSchema,
  },
  parseChannelDeliverySnapshot: {
    modelName: "ChannelDeliverySnapshot",
    schema: channelDeliverySnapshotSchema,
  },
  parseChannelDeliveryPartSnapshot: {
    modelName: "ChannelDeliveryPartSnapshot",
    schema: channelDeliveryPartSnapshotSchema,
  },
  parseChannelDeliveryPartClaimRequest: {
    modelName: "ChannelDeliveryPartClaimRequest",
    schema: channelDeliveryPartClaimRequestSchema,
  },
  parseChannelDeliveryPartAcknowledgement: {
    modelName: "ChannelDeliveryPartAcknowledgement",
    schema: channelDeliveryPartAcknowledgementSchema,
  },
  parseChannelDeliveryPlanSnapshot: {
    modelName: "ChannelDeliveryPlanSnapshot",
    schema: channelDeliveryPlanSnapshotSchema,
  },
  parseChannelTurnCancelRequest: {
    modelName: "ChannelTurnCancelRequest",
    schema: channelTurnCancelRequestSchema,
  },
  parseChannelTurnCancelReceipt: {
    modelName: "ChannelTurnCancelReceipt",
    schema: channelTurnCancelReceiptSchema,
  },
  parseChannelErrorResponse: {
    modelName: "ChannelErrorResponse",
    schema: channelErrorResponseSchema,
  },
  parseMemoryRecord: {
    modelName: "MemoryRecord",
    schema: memoryRecordSchema,
  },
  parseMemoryProposal: {
    modelName: "MemoryProposal",
    schema: memoryProposalSchema,
  },
  parseMemorySource: {
    modelName: "MemorySource",
    schema: memorySourceSchema,
  },
  parseMemoryChannelAttribution: {
    modelName: "MemoryChannelAttribution",
    schema: memoryChannelAttributionSchema,
  },
  parseSkillDefinition: {
    modelName: "SkillDefinition",
    schema: skillDefinitionSchema,
  },
  parseSkillRunSnapshot: {
    modelName: "SkillRunSnapshot",
    schema: skillRunSnapshotSchema,
  },
  parsePluginSnapshot: {
    modelName: "PluginSnapshot",
    schema: pluginSnapshotSchema,
  },
  parseMcpCapabilitySnapshot: {
    modelName: "McpCapabilitySnapshot",
    schema: mcpCapabilitySnapshotSchema,
  },
  parseMcpConnectionSnapshot: {
    modelName: "McpConnectionSnapshot",
    schema: mcpConnectionSnapshotSchema,
  },
  parseCommandEnvelope: {
    modelName: "CommandModel",
    schema: commandEnvelopeSchema,
  },
  parseEventEnvelope: {
    modelName: "EventModel",
    schema: eventEnvelopeSchema,
  },
};

export const standaloneEnumSchemas: Record<string, z.ZodTypeAny> = {
  ChannelTurnStatus: channelTurnStatusSchema,
  ChannelDeliveryStatus: channelDeliveryStatusSchema,
  ChannelDeliveryPartKind: channelDeliveryPartKindSchema,
  ChannelDeliveryPartStatus: channelDeliveryPartStatusSchema,
};

export type JsonSchemaDef = {
  enum?: unknown[];
  const?: unknown;
  $ref?: string;
  anyOf?: JsonSchemaDef[];
  allOf?: JsonSchemaDef[];
  oneOf?: JsonSchemaDef[];
  type?: string;
  items?: JsonSchemaDef;
  properties?: Record<string, JsonSchemaDef>;
};

/**
 * Recursively extracts all enum paths from a JSON Schema definition.
 * Combines all branches of anyOf, allOf, and oneOf into unified enum value sets.
 */
export function collectJsonSchemaEnumPaths(
  schema: JsonSchemaDef | null | undefined,
  currentPath = "",
  defs: Record<string, JsonSchemaDef> = {},
  ancestors = new Set<unknown>(),
): Record<string, string[]> {
  const paths: Record<string, string[]> = {};
  if (!schema || typeof schema !== "object" || ancestors.has(schema)) {
    return paths;
  }
  const nextAncestors = new Set(ancestors).add(schema);

  if (Array.isArray(schema.enum)) {
    const vals = schema.enum.filter((v): v is string => typeof v === "string");
    if (vals.length > 0) {
      paths[currentPath] = vals.sort();
    }
  } else if (typeof schema.const === "string") {
    paths[currentPath] = [schema.const];
  }

  if (typeof schema.$ref === "string" && schema.$ref.startsWith("#/$defs/")) {
    const targetName = schema.$ref.slice(8);
    if (defs[targetName]) {
      const sub = collectJsonSchemaEnumPaths(
        defs[targetName],
        currentPath,
        defs,
        nextAncestors,
      );
      for (const [k, v] of Object.entries(sub)) {
        paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
      }
    }
  }

  for (const key of ["anyOf", "allOf", "oneOf"] as const) {
    const branches = schema[key];
    if (Array.isArray(branches)) {
      for (const subSchema of branches) {
        const sub = collectJsonSchemaEnumPaths(
          subSchema,
          currentPath,
          defs,
          nextAncestors,
        );
        for (const [k, v] of Object.entries(sub)) {
          paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
        }
      }
    }
  }

  if (schema.type === "array" && schema.items) {
    const sub = collectJsonSchemaEnumPaths(
      schema.items,
      `${currentPath}[]`,
      defs,
      nextAncestors,
    );
    for (const [k, v] of Object.entries(sub)) {
      paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
    }
  }

  if (schema.properties && typeof schema.properties === "object") {
    for (const [prop, propSchema] of Object.entries(schema.properties)) {
      const nextPath = currentPath ? `${currentPath}.${prop}` : prop;
      const sub = collectJsonSchemaEnumPaths(
        propSchema,
        nextPath,
        defs,
        nextAncestors,
      );
      for (const [k, v] of Object.entries(sub)) {
        paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
      }
    }
  }

  return paths;
}

type ZodInspectable = {
  options?: unknown[];
  element?: ZodInspectable;
  shape?: Record<string, unknown>;
  _def?: {
    type?: string;
    entries?: Record<string, unknown>;
    values?: unknown[];
    value?: unknown;
    options?: unknown[];
    element?: ZodInspectable;
    innerType?: ZodInspectable;
    schema?: ZodInspectable;
    shape?: Record<string, unknown>;
  };
};

/**
 * Recursively extracts all enum paths from a Zod schema.
 * Correctly distinguishes enum options from union schema options and combines union branches.
 */
export function collectZodEnumPaths(
  schema: unknown,
  currentPath = "",
  ancestors = new Set<unknown>(),
): Record<string, string[]> {
  const paths: Record<string, string[]> = {};
  if (!schema || typeof schema !== "object" || ancestors.has(schema)) {
    return paths;
  }
  const nextAncestors = new Set(ancestors).add(schema);

  let curr: ZodInspectable | undefined = schema as ZodInspectable | undefined;
  while (curr && curr._def) {
    if (curr._def.type === "enum") {
      const vals: string[] = curr.options
        ? (curr.options as string[])
        : Object.keys(curr._def.entries || {});
      paths[currentPath] = vals.slice().sort();
      return paths;
    }
    if (curr._def.type === "literal") {
      const vals: string[] = Array.isArray(curr._def.values)
        ? curr._def.values.map(String)
        : curr._def.value !== undefined
          ? [String(curr._def.value)]
          : [];
      paths[currentPath] = vals.sort();
      return paths;
    }
    if (curr._def.type === "union" && Array.isArray(curr._def.options)) {
      for (const opt of curr._def.options) {
        const sub = collectZodEnumPaths(opt, currentPath, nextAncestors);
        for (const [k, v] of Object.entries(sub)) {
          paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
        }
      }
      return paths;
    }
    if (curr._def.type === "array") {
      const itemSchema = curr._def.element ?? curr.element;
      return collectZodEnumPaths(itemSchema, `${currentPath}[]`, nextAncestors);
    }
    if (curr._def.type === "object" || curr.shape) {
      const shape = curr.shape || curr._def.shape;
      if (shape && typeof shape === "object") {
        for (const [prop, propSchema] of Object.entries(shape)) {
          const nextPath = currentPath ? `${currentPath}.${prop}` : prop;
          const sub = collectZodEnumPaths(propSchema, nextPath, nextAncestors);
          for (const [k, v] of Object.entries(sub)) {
            paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
          }
        }
        return paths;
      }
    }
    if (curr._def.innerType) {
      curr = curr._def.innerType;
      continue;
    }
    if (curr._def.schema) {
      curr = curr._def.schema;
      continue;
    }
    break;
  }
  return paths;
}
