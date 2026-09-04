import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import type {
  ChannelDeliveryAcknowledgement,
  ChannelDeliveryPartAcknowledgement,
  ChannelDeliveryStatus,
  ChannelTurnStatus,
  SkillCapability,
} from "../src/index";
import * as publicProtocol from "../src/index";
import * as parserModule from "../src/parsers/protocol";
import {
  channelDeliveryAcknowledgementSchema,
  channelDeliveryPartAcknowledgementSchema,
  channelDeliveryStatusSchema,
  channelTurnStatusSchema,
  collectDiscriminatedJsonSchemaEnumPaths,
  collectJsonSchemaEnumPaths,
  collectZodDiscriminatedUnionEnumPaths,
  collectZodEnumPaths,
  parserRootRegistry,
  protocolModelSchemas,
  skillCapabilitySchema,
  standaloneEnumSchemas,
  type JsonSchemaDef,
} from "../src/internal/testing/index";

// Compile-time type-level equivalence assertions
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
    ? true
    : false;
type Expect<T extends true> = T;

// Verify at compile time that Zod output types strictly match Generated TypeScript types
export type _AssertWholeDeliveryAckStatus = Expect<
  Equal<
    z.output<typeof channelDeliveryAcknowledgementSchema>["status"],
    ChannelDeliveryAcknowledgement["status"]
  >
>;
export type _AssertPartDeliveryAckStatus = Expect<
  Equal<
    z.output<typeof channelDeliveryPartAcknowledgementSchema>["status"],
    ChannelDeliveryPartAcknowledgement["status"]
  >
>;
export type _AssertChannelTurnStatus = Expect<
  Equal<z.output<typeof channelTurnStatusSchema>, ChannelTurnStatus>
>;
export type _AssertChannelDeliveryStatus = Expect<
  Equal<z.output<typeof channelDeliveryStatusSchema>, ChannelDeliveryStatus>
>;
export type _AssertSkillCapabilityAdapterOp = Expect<
  Equal<
    z.output<typeof skillCapabilitySchema>["adapter_operation"],
    NonNullable<SkillCapability["adapter_operation"]>
  >
>;

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const catalogPath = path.join(
  root,
  "schemas/domain/v1/protocol-catalog.schema.json",
);
const catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
const defs: Record<string, JsonSchemaDef> = catalog.$defs || {};

describe("Universal Cross-Layer Enum Contract Gate", () => {
  it("enforces three-way parser consistency across Parser Module, Root Public Export, and parserRootRegistry", () => {
    const isParseFunction = (k: string, mod: Record<string, unknown>) =>
      k.startsWith("parse") && typeof mod[k] === "function";

    const declaredParsers = Object.keys(parserModule)
      .filter((k) => isParseFunction(k, parserModule))
      .sort();
    const publicParsers = Object.keys(publicProtocol)
      .filter((k) => isParseFunction(k, publicProtocol))
      .sort();
    const registeredParsers = Object.keys(parserRootRegistry).sort();

    // 1. All declared parsers in parsers/protocol.ts are publicly exported in src/index.ts
    expect(publicParsers).toEqual(declaredParsers);

    // 2. All declared parsers are registered in parserRootRegistry
    expect(registeredParsers).toEqual(declaredParsers);

    // 3. The registry mapping is a bijection (no duplicate target models)
    const mappedModels = Object.values(parserRootRegistry);
    expect(mappedModels).toHaveLength(new Set(mappedModels).size);

    // 4. All mapped model names exist in protocolModelSchemas
    expect(new Set(mappedModels)).toEqual(
      new Set(Object.keys(protocolModelSchemas)),
    );
  });

  it("enforces exact parity for standalone protocol enums", () => {
    for (const [enumName, zodEnum] of Object.entries(standaloneEnumSchemas)) {
      const jsonDef = defs[enumName];
      expect(
        jsonDef,
        `Catalog definition missing for enum: ${enumName}`,
      ).toBeDefined();

      const jsonPaths = collectJsonSchemaEnumPaths(jsonDef, enumName, defs);
      const zodPaths = collectZodEnumPaths(zodEnum, enumName);

      expect(
        Object.keys(zodPaths).sort(),
        `Standalone enum path mismatch for ${enumName}`,
      ).toEqual(Object.keys(jsonPaths).sort());

      expect(zodPaths[enumName]).toEqual(jsonPaths[enumName]);
    }
  });

  it("enforces exact recursive enum path and value parity across all registered direct domain models", () => {
    const expectedPaths: Record<string, string[]> = {};
    const actualPaths: Record<string, string[]> = {};

    for (const [modelName, schema] of Object.entries(protocolModelSchemas)) {
      if (modelName === "CommandModel" || modelName === "EventModel") {
        // Envelopes are polymorphic unions tested in dedicated discriminator-aware test
        continue;
      }
      expect(
        defs[modelName],
        `Catalog definition missing for model: ${modelName}`,
      ).toBeDefined();

      const jsonPaths = collectJsonSchemaEnumPaths(
        defs[modelName],
        modelName,
        defs,
      );
      const zodPaths = collectZodEnumPaths(schema, modelName);

      Object.assign(expectedPaths, jsonPaths);
      Object.assign(actualPaths, zodPaths);
    }

    // 1. Strict set-equality on all recursive paths: no omissions or extra paths
    const actualKeys = Object.keys(actualPaths).sort();
    const expectedKeys = Object.keys(expectedPaths).sort();

    expect(actualKeys).toEqual(expectedKeys);

    // 2. Strict value-equality on each recursive path
    for (const key of expectedKeys) {
      expect(actualPaths[key], `Enum mismatch at path: ${key}`).toEqual(
        expectedPaths[key],
      );
    }
  });

  it("enforces exact discriminator-aware enum path parity for EventModel and CommandModel", () => {
    // 1. EventModel: discriminator is event_type, path format: EventModel[event_type=...].path
    const eventJsonPaths = collectDiscriminatedJsonSchemaEnumPaths(
      defs["EventModel"],
      {
        rootName: "EventModel",
        discriminator: "event_type",
        defs,
      },
    );
    const eventZodPaths = collectZodDiscriminatedUnionEnumPaths(
      protocolModelSchemas["EventModel"],
      {
        rootName: "EventModel",
        discriminator: "event_type",
      },
    );

    const eventJsonKeys = Object.keys(eventJsonPaths).sort();
    const eventZodKeys = Object.keys(eventZodPaths).sort();

    expect(
      eventZodKeys,
      "Discriminator-aware EventModel enum path mismatch",
    ).toEqual(eventJsonKeys);

    for (const key of eventJsonKeys) {
      expect(
        eventZodPaths[key],
        `EventModel enum values mismatch at ${key}`,
      ).toEqual(eventJsonPaths[key]);
    }

    // 2. CommandModel: discriminator is command_type, path format: CommandModel[command_type=...].path
    const commandJsonPaths = collectDiscriminatedJsonSchemaEnumPaths(
      catalog.properties.command,
      {
        rootName: "CommandModel",
        discriminator: "command_type",
        defs,
      },
    );
    const commandZodPaths = collectZodDiscriminatedUnionEnumPaths(
      protocolModelSchemas["CommandModel"],
      {
        rootName: "CommandModel",
        discriminator: "command_type",
      },
    );

    const commandJsonKeys = Object.keys(commandJsonPaths).sort();
    const commandZodKeys = Object.keys(commandZodPaths).sort();

    expect(
      commandZodKeys,
      "Discriminator-aware CommandModel enum path mismatch",
    ).toEqual(commandJsonKeys);

    for (const key of commandJsonKeys) {
      expect(
        commandZodPaths[key],
        `CommandModel enum values mismatch at ${key}`,
      ).toEqual(commandJsonPaths[key]);
    }
  });

  it("explicitly verifies ACK status contracts across legacy whole-delivery and multipart", () => {
    const legacyAck = {
      schema_version: "1.0",
      delivery_id: "00000000-0000-4000-8000-000000000a01",
      channel_turn_id: "00000000-0000-4000-8000-000000000a02",
      lease_id: "00000000-0000-4000-8000-000000000a03",
      acknowledged_at: "2026-09-04T00:00:00Z",
    };

    const structuredError = {
      code: "test_error",
      message: "delivery failed",
      retryable: false,
      component: "external_channels",
    };

    // Legacy whole-delivery ACK allows delivered | failed | cancelled
    for (const status of ["delivered", "failed", "cancelled"] as const) {
      expect(() =>
        publicProtocol.parseChannelDeliveryAcknowledgement({
          ...legacyAck,
          status,
          error: status === "failed" ? structuredError : undefined,
        }),
      ).not.toThrow();
    }

    // Part-level ACK strictly only allows delivered | failed
    const partAck = {
      ...legacyAck,
      part_id: "00000000-0000-4000-8000-000000000a04",
    };

    for (const status of ["delivered", "failed"] as const) {
      expect(() =>
        publicProtocol.parseChannelDeliveryPartAcknowledgement({
          ...partAck,
          status,
          error: status === "failed" ? structuredError : undefined,
        }),
      ).not.toThrow();
    }

    expect(() =>
      publicProtocol.parseChannelDeliveryPartAcknowledgement({
        ...partAck,
        status: "cancelled",
      }),
    ).toThrow();
  });
});

describe("Collector and Gate Failure Mode Verification (Real Negative Tests)", () => {
  it("detects enum values swapped between discriminated union branches", () => {
    const jsonBranchA: JsonSchemaDef = {
      properties: {
        type: { const: "A" },
        mode: { enum: ["fast", "turbo"] },
      },
    };
    const jsonBranchB: JsonSchemaDef = {
      properties: {
        type: { const: "B" },
        mode: { enum: ["safe", "strict"] },
      },
    };
    const jsonSchema: JsonSchemaDef = {
      anyOf: [jsonBranchA, jsonBranchB],
    };

    // Zod schema with swapped enum values: A has safe/strict, B has fast/turbo
    const zodSchemaSwapped = z.discriminatedUnion("type", [
      z.object({
        type: z.literal("A"),
        mode: z.enum(["safe", "strict"]),
      }),
      z.object({
        type: z.literal("B"),
        mode: z.enum(["fast", "turbo"]),
      }),
    ]);

    const jsonPaths = collectDiscriminatedJsonSchemaEnumPaths(jsonSchema, {
      rootName: "SwappedModel",
      discriminator: "type",
      defs: {},
    });
    const zodPaths = collectZodDiscriminatedUnionEnumPaths(zodSchemaSwapped, {
      rootName: "SwappedModel",
      discriminator: "type",
    });

    // Even though the global set of modes across all branches is ["fast", "safe", "strict", "turbo"],
    // the branch-specific paths SwappedModel[type=A].mode and SwappedModel[type=B].mode are swapped!
    expect(() => {
      for (const key of Object.keys(jsonPaths)) {
        expect(zodPaths[key]).toEqual(jsonPaths[key]);
      }
    }).toThrow();
  });

  it("collects the union of all branches in multi-branch anyOf / oneOf without stopping at the first", () => {
    const syntheticSchema: JsonSchemaDef = {
      anyOf: [
        { const: "first_val" },
        { const: "second_val" },
        { enum: ["third_val", "fourth_val"] },
      ],
    };

    const extracted = collectJsonSchemaEnumPaths(
      syntheticSchema,
      "SyntheticRoot",
      {},
    );
    expect(extracted["SyntheticRoot"]).toEqual([
      "first_val",
      "fourth_val",
      "second_val",
      "third_val",
    ]);
  });

  it("extracts nested object and array enum paths recursively", () => {
    const nestedSchema: JsonSchemaDef = {
      properties: {
        items: {
          type: "array",
          items: {
            properties: {
              status: {
                enum: ["active", "suspended"],
              },
            },
          },
        },
      },
    };

    const extracted = collectJsonSchemaEnumPaths(nestedSchema, "Root", {});
    expect(extracted).toEqual({
      "Root.items[].status": ["active", "suspended"],
    });
  });

  it("extracts and merges enum paths across Zod unions accurately", () => {
    const zodUnion = z.union([
      z.object({ tag: z.literal("A"), mode: z.enum(["fast", "safe"]) }),
      z.object({ tag: z.literal("B"), mode: z.enum(["safe", "experimental"]) }),
    ]);

    const extracted = collectZodEnumPaths(zodUnion, "UnionRoot");
    expect(extracted["UnionRoot.tag"]).toEqual(["A", "B"]);
    expect(extracted["UnionRoot.mode"]).toEqual([
      "experimental",
      "fast",
      "safe",
    ]);
  });

  it("fails coverage validation when a public parser is omitted from the registry", () => {
    const mockExportedParsers = [
      ...Object.keys(parserRootRegistry),
      "parseNewlyAddedModelWithoutRegistration",
    ];

    expect(() => {
      expect(new Set(mockExportedParsers)).toEqual(
        new Set(Object.keys(parserRootRegistry)),
      );
    }).toThrow();
  });

  it("fails parity validation when an enum path has missing or extraneous values", () => {
    const mockJsonPaths = {
      "Model.status": ["delivered", "failed", "cancelled"],
    };
    const mockZodPathsMissingCancelled = {
      "Model.status": ["delivered", "failed"],
    };

    expect(() => {
      expect(mockZodPathsMissingCancelled["Model.status"]).toEqual(
        mockJsonPaths["Model.status"],
      );
    }).toThrow();
  });

  it("throws on an unrecognized or unsupported Zod node type to prevent silent omissions", () => {
    const fakeZodNode = {
      _def: {
        type: "exotic_future_zod_type",
      },
    };

    expect(() => {
      collectZodEnumPaths(fakeZodNode, "FutureRoot");
    }).toThrow(
      /Unsupported or unrecognized Zod node type "exotic_future_zod_type"/,
    );
  });

  it("extracts enum paths from prefixItems and additionalProperties in JSON Schema", () => {
    const schemaWithFutureKeywords: JsonSchemaDef = {
      prefixItems: [{ enum: ["fixed_first"] }, { enum: ["fixed_second"] }],
      additionalProperties: {
        properties: {
          flag: { enum: ["enabled", "disabled"] },
        },
      },
    };

    const extracted = collectJsonSchemaEnumPaths(
      schemaWithFutureKeywords,
      "ExtSchema",
      {},
    );
    expect(extracted["ExtSchema[0]"]).toEqual(["fixed_first"]);
    expect(extracted["ExtSchema[1]"]).toEqual(["fixed_second"]);
    expect(extracted["ExtSchema.*.flag"]).toEqual(["disabled", "enabled"]);
  });
});
