/**
 * Internal testing registry and recursive enum path collectors for contract parity testing.
 * Not exported from the root `@chatwaifu/protocol` package.
 */

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
  channelMessageKindSchema,
  channelPresentationPolicySchema,
  channelPresentationProfileSchema,
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
} from "../../parsers/protocol";

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
  channelMessageKindSchema,
  channelPresentationPolicySchema,
  channelPresentationProfileSchema,
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

/**
 * Registry of all root model schemas that back public parsers.
 */
export const protocolModelSchemas = {
  AudioFrameHeader: audioFrameHeaderSchema,
  AvatarCue: avatarCueSchema,
  AvatarCapabilityManifest: avatarCapabilityManifestSchema,
  AvatarInteractionEvent: avatarInteractionEventSchema,
  SessionSnapshot: sessionSnapshotSchema,
  CharacterKernelSnapshot: characterKernelSnapshotSchema,
  ChannelProviderRegistration: channelProviderRegistrationSchema,
  ChannelPresentationPolicy: channelPresentationPolicySchema,
  ChannelAuthorizationStartRequest: channelAuthorizationStartRequestSchema,
  ChannelAuthorizationVerificationRequest:
    channelAuthorizationVerificationRequestSchema,
  ChannelAuthorizationSnapshot: channelAuthorizationSnapshotSchema,
  ChannelConnectionConfiguration: channelConnectionConfigurationSchema,
  ChannelConnectionSnapshot: channelConnectionSnapshotSchema,
  ChannelGatewayStatusSnapshot: channelGatewayStatusSnapshotSchema,
  ChannelInboundTextMessage: channelInboundTextMessageSchema,
  ChannelTurnReceipt: channelTurnReceiptSchema,
  ChannelTurnSnapshot: channelTurnSnapshotSchema,
  ChannelDeliveryAcknowledgement: channelDeliveryAcknowledgementSchema,
  ChannelDeliveryClaimRequest: channelDeliveryClaimRequestSchema,
  ChannelDeliverySnapshot: channelDeliverySnapshotSchema,
  ChannelDeliveryPartSnapshot: channelDeliveryPartSnapshotSchema,
  ChannelDeliveryPartClaimRequest: channelDeliveryPartClaimRequestSchema,
  ChannelDeliveryPartAcknowledgement: channelDeliveryPartAcknowledgementSchema,
  ChannelDeliveryPlanSnapshot: channelDeliveryPlanSnapshotSchema,
  ChannelTurnCancelRequest: channelTurnCancelRequestSchema,
  ChannelTurnCancelReceipt: channelTurnCancelReceiptSchema,
  ChannelErrorResponse: channelErrorResponseSchema,
  MemoryRecord: memoryRecordSchema,
  MemoryProposal: memoryProposalSchema,
  MemorySource: memorySourceSchema,
  MemoryChannelAttribution: memoryChannelAttributionSchema,
  SkillDefinition: skillDefinitionSchema,
  SkillRunSnapshot: skillRunSnapshotSchema,
  PluginSnapshot: pluginSnapshotSchema,
  McpCapabilitySnapshot: mcpCapabilitySnapshotSchema,
  McpConnectionSnapshot: mcpConnectionSnapshotSchema,
  CommandModel: commandEnvelopeSchema,
  EventModel: eventEnvelopeSchema,
} as const;

/**
 * Registry of standalone protocol enum schemas.
 */
export const protocolEnumSchemas = {
  ChannelPresentationProfile: channelPresentationProfileSchema,
  ChannelTurnStatus: channelTurnStatusSchema,
  ChannelDeliveryStatus: channelDeliveryStatusSchema,
  ChannelDeliveryPartKind: channelDeliveryPartKindSchema,
  ChannelDeliveryPartStatus: channelDeliveryPartStatusSchema,
  ChannelMessageKind: channelMessageKindSchema,
} as const;

export const standaloneEnumSchemas = protocolEnumSchemas;

/**
 * Maps every public parseXxx function to its corresponding root model key in protocolModelSchemas.
 */
export const parserRootRegistry: Record<
  string,
  keyof typeof protocolModelSchemas
> = {
  parseAudioFrameHeader: "AudioFrameHeader",
  parseAvatarCue: "AvatarCue",
  parseAvatarCapabilityManifest: "AvatarCapabilityManifest",
  parseAvatarInteractionEvent: "AvatarInteractionEvent",
  parseSessionSnapshot: "SessionSnapshot",
  parseCharacterKernelSnapshot: "CharacterKernelSnapshot",
  parseChannelProviderRegistration: "ChannelProviderRegistration",
  parseChannelPresentationPolicy: "ChannelPresentationPolicy",
  parseChannelAuthorizationStartRequest: "ChannelAuthorizationStartRequest",
  parseChannelAuthorizationVerificationRequest:
    "ChannelAuthorizationVerificationRequest",
  parseChannelAuthorizationSnapshot: "ChannelAuthorizationSnapshot",
  parseChannelConnectionConfiguration: "ChannelConnectionConfiguration",
  parseChannelConnectionSnapshot: "ChannelConnectionSnapshot",
  parseChannelGatewayStatusSnapshot: "ChannelGatewayStatusSnapshot",
  parseChannelInboundTextMessage: "ChannelInboundTextMessage",
  parseChannelTurnReceipt: "ChannelTurnReceipt",
  parseChannelTurnSnapshot: "ChannelTurnSnapshot",
  parseChannelDeliveryAcknowledgement: "ChannelDeliveryAcknowledgement",
  parseChannelDeliveryClaimRequest: "ChannelDeliveryClaimRequest",
  parseChannelDeliverySnapshot: "ChannelDeliverySnapshot",
  parseChannelDeliveryPartSnapshot: "ChannelDeliveryPartSnapshot",
  parseChannelDeliveryPartClaimRequest: "ChannelDeliveryPartClaimRequest",
  parseChannelDeliveryPartAcknowledgement: "ChannelDeliveryPartAcknowledgement",
  parseChannelDeliveryPlanSnapshot: "ChannelDeliveryPlanSnapshot",
  parseChannelTurnCancelRequest: "ChannelTurnCancelRequest",
  parseChannelTurnCancelReceipt: "ChannelTurnCancelReceipt",
  parseChannelErrorResponse: "ChannelErrorResponse",
  parseMemoryRecord: "MemoryRecord",
  parseMemoryProposal: "MemoryProposal",
  parseMemorySource: "MemorySource",
  parseMemoryChannelAttribution: "MemoryChannelAttribution",
  parseSkillDefinition: "SkillDefinition",
  parseSkillRunSnapshot: "SkillRunSnapshot",
  parsePluginSnapshot: "PluginSnapshot",
  parseMcpCapabilitySnapshot: "McpCapabilitySnapshot",
  parseMcpConnectionSnapshot: "McpConnectionSnapshot",
  parseCommandEnvelope: "CommandModel",
  parseEventEnvelope: "EventModel",
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
  prefixItems?: JsonSchemaDef[];
  additionalProperties?: boolean | JsonSchemaDef;
  properties?: Record<string, JsonSchemaDef>;
};

/**
 * Recursively extracts all enum paths from a JSON Schema definition.
 * Combines all branches of anyOf, allOf, and oneOf into unified enum value sets.
 * Also supports items, prefixItems, and additionalProperties schemas.
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

  if (Array.isArray(schema.prefixItems)) {
    schema.prefixItems.forEach((itemSchema, idx) => {
      const sub = collectJsonSchemaEnumPaths(
        itemSchema,
        `${currentPath}[${idx}]`,
        defs,
        nextAncestors,
      );
      for (const [k, v] of Object.entries(sub)) {
        paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
      }
    });
  }

  if (
    schema.additionalProperties &&
    typeof schema.additionalProperties === "object"
  ) {
    const sub = collectJsonSchemaEnumPaths(
      schema.additionalProperties,
      `${currentPath}.*`,
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

/**
 * Collects enum paths from a polymorphic JSON Schema union where each branch
 * is identified by a discriminator field (e.g. event_type or command_type).
 * Generates paths carrying discriminator identity: Root[discriminator=value].path
 */
export function collectDiscriminatedJsonSchemaEnumPaths(
  schema: JsonSchemaDef | null | undefined,
  options: {
    rootName: string;
    discriminator: string;
    defs: Record<string, JsonSchemaDef>;
  },
): Record<string, string[]> {
  const { rootName, discriminator, defs } = options;
  const paths: Record<string, string[]> = {};
  if (!schema) return paths;

  const branches = schema.anyOf || schema.oneOf || [schema];

  for (const branchRef of branches) {
    const branch =
      branchRef.$ref && branchRef.$ref.startsWith("#/$defs/")
        ? defs[branchRef.$ref.slice(8)]
        : branchRef;
    if (!branch || !branch.properties) continue;

    const discProp = branch.properties[discriminator];
    let discVals: string[] = [];
    if (discProp) {
      if (typeof discProp.const === "string") {
        discVals = [discProp.const];
      } else if (Array.isArray(discProp.enum)) {
        discVals = discProp.enum.filter(
          (v): v is string => typeof v === "string",
        );
      } else if (discProp.$ref && discProp.$ref.startsWith("#/$defs/")) {
        const target = defs[discProp.$ref.slice(8)];
        if (target && Array.isArray(target.enum)) {
          discVals = target.enum.filter(
            (v): v is string => typeof v === "string",
          );
        }
      }
    }

    for (const dVal of discVals) {
      const branchPrefix = `${rootName}[${discriminator}=${dVal}]`;
      paths[`${branchPrefix}.${discriminator}`] = [dVal];

      for (const [prop, pSchema] of Object.entries(branch.properties)) {
        if (prop === discriminator) continue;
        const sub = collectJsonSchemaEnumPaths(
          pSchema,
          `${branchPrefix}.${prop}`,
          defs,
        );
        for (const [k, v] of Object.entries(sub)) {
          paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
        }
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
    getter?: () => ZodInspectable;
    shape?: Record<string, unknown>;
  };
};

/** Known leaf/non-enum node types in Zod AST */
const KNOWN_NON_ENUM_TYPES = new Set([
  "string",
  "number",
  "boolean",
  "date",
  "bigint",
  "unknown",
  "any",
  "void",
  "null",
  "undefined",
  "never",
  "record",
  "custom",
  "nan",
  "symbol",
]);

/**
 * Recursively extracts all enum paths from a Zod schema.
 * Correctly distinguishes enum options from union schema options and combines union branches.
 * Throws on unknown or unhandled Zod AST node types to prevent silent omissions.
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
    const nodeType = curr._def.type;

    if (nodeType === "enum") {
      const vals: string[] = curr.options
        ? (curr.options as string[])
        : Object.keys(curr._def.entries || {});
      paths[currentPath] = vals.slice().sort();
      return paths;
    }
    if (nodeType === "literal") {
      const vals: string[] = Array.isArray(curr._def.values)
        ? curr._def.values.map(String)
        : curr._def.value !== undefined
          ? [String(curr._def.value)]
          : [];
      paths[currentPath] = vals.sort();
      return paths;
    }
    if (nodeType === "union" && Array.isArray(curr._def.options)) {
      for (const opt of curr._def.options) {
        const sub = collectZodEnumPaths(opt, currentPath, nextAncestors);
        for (const [k, v] of Object.entries(sub)) {
          paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
        }
      }
      return paths;
    }
    if (nodeType === "array") {
      const itemSchema = curr._def.element ?? curr.element;
      return collectZodEnumPaths(itemSchema, `${currentPath}[]`, nextAncestors);
    }
    if (nodeType === "object" || curr.shape) {
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
    if (nodeType === "lazy") {
      const getter = curr._def?.getter;
      if (typeof getter === "function") {
        curr = getter();
        continue;
      }
    }

    if (nodeType && KNOWN_NON_ENUM_TYPES.has(nodeType)) {
      return paths;
    }

    throw new Error(
      `Unsupported or unrecognized Zod node type "${nodeType}" at path "${currentPath}". ` +
        `Please update collectZodEnumPaths to support this AST structure.`,
    );
  }
  return paths;
}

/**
 * Collects enum paths from a Zod union or discriminated union where each branch
 * is identified by a discriminator field (e.g. event_type or command_type).
 * Generates paths carrying discriminator identity: Root[discriminator=value].path
 */
export function collectZodDiscriminatedUnionEnumPaths(
  schema: unknown,
  options: {
    rootName: string;
    discriminator: string;
  },
): Record<string, string[]> {
  const { rootName, discriminator } = options;
  const paths: Record<string, string[]> = {};

  const branchSchemas: unknown[] = [];
  function extractBranches(s: unknown) {
    if (!s || typeof s !== "object") return;
    const insp = s as ZodInspectable;
    if (insp._def?.type === "union" && Array.isArray(insp._def.options)) {
      for (const opt of insp._def.options) {
        extractBranches(opt);
      }
      return;
    }
    branchSchemas.push(s);
  }
  extractBranches(schema);

  for (const branch of branchSchemas) {
    let curr = branch as ZodInspectable | undefined;
    while (curr && curr._def?.innerType) curr = curr._def.innerType;
    const shape = curr?.shape || curr?._def?.shape;
    if (!shape) continue;

    const discField = shape[discriminator] as ZodInspectable | undefined;
    let discVals: string[] = [];
    if (discField) {
      if (discField._def?.type === "literal") {
        discVals = Array.isArray(discField._def.values)
          ? discField._def.values.map(String)
          : discField._def.value !== undefined
            ? [String(discField._def.value)]
            : [];
      } else if (discField._def?.type === "enum") {
        discVals = discField.options
          ? (discField.options as string[])
          : Object.keys(discField._def.entries || {});
      }
    }

    for (const dVal of discVals) {
      const branchPrefix = `${rootName}[${discriminator}=${dVal}]`;
      paths[`${branchPrefix}.${discriminator}`] = [dVal];

      for (const [prop, propSchema] of Object.entries(shape)) {
        if (prop === discriminator) continue;
        const sub = collectZodEnumPaths(propSchema, `${branchPrefix}.${prop}`);
        for (const [k, v] of Object.entries(sub)) {
          paths[k] = Array.from(new Set([...(paths[k] || []), ...v])).sort();
        }
      }
    }
  }

  return paths;
}
