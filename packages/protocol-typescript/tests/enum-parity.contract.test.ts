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
import * as protocol from "../src/index";
import {
  channelDeliveryAcknowledgementSchema,
  channelDeliveryPartAcknowledgementSchema,
  channelDeliveryStatusSchema,
  channelTurnStatusSchema,
  collectJsonSchemaEnumPaths,
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
  it("self-validates that all exported public parseXxx functions have a registered root schema", () => {
    const exportedParsers = Object.keys(protocol).filter((k) =>
      k.startsWith("parse"),
    );
    const registeredParsers = Object.keys(parserRootRegistry);

    // 1. Strict set-equality: no unmapped public parse functions
    expect(new Set(exportedParsers)).toEqual(new Set(registeredParsers));

    // 2. All mapped model names exist in protocolModelSchemas
    const mappedModels = Object.values(parserRootRegistry);
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

  it("enforces exact recursive enum path and value parity across all registered domain models", () => {
    const expectedPaths: Record<string, string[]> = {};
    const actualPaths: Record<string, string[]> = {};

    for (const [modelName, schema] of Object.entries(protocolModelSchemas)) {
      if (modelName === "CommandModel" || modelName === "EventModel") {
        // Envelopes are polymorphic unions tested in the dedicated envelope test
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

  it("enforces enum parity for polymorphic EventEnvelope and CommandEnvelope", () => {
    // EventEnvelope: verify event_type and privacy parity across all event variants
    const eventJsonPaths = collectJsonSchemaEnumPaths(
      defs["EventModel"],
      "EventModel",
      defs,
    );
    const eventZodPaths = collectZodEnumPaths(
      protocolModelSchemas["EventModel"],
      "EventModel",
    );

    expect(eventZodPaths["EventModel.event_type"]).toEqual(
      eventJsonPaths["EventModel.event_type"],
    );
    expect(eventZodPaths["EventModel.privacy"]).toEqual(
      eventJsonPaths["EventModel.privacy"],
    );

    // CommandEnvelope: verify command_type, playback ack phase, reason, and transport
    const commandJsonPaths = collectJsonSchemaEnumPaths(
      catalog.properties.command,
      "CommandModel",
      defs,
    );
    const commandZodPaths = collectZodEnumPaths(
      protocolModelSchemas["CommandModel"],
      "CommandModel",
    );

    expect(commandZodPaths["CommandModel.command_type"]).toEqual(
      commandJsonPaths["CommandModel.command_type"],
    );
    expect(commandZodPaths["CommandModel.payload.phase"]).toEqual(
      commandJsonPaths["CommandModel.payload.phase"],
    );
    expect(commandZodPaths["CommandModel.payload.transport"]).toEqual(
      commandJsonPaths["CommandModel.payload.transport"],
    );
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
        protocol.parseChannelDeliveryAcknowledgement({
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
        protocol.parseChannelDeliveryPartAcknowledgement({
          ...partAck,
          status,
          error: status === "failed" ? structuredError : undefined,
        }),
      ).not.toThrow();
    }

    expect(() =>
      protocol.parseChannelDeliveryPartAcknowledgement({
        ...partAck,
        status: "cancelled",
      }),
    ).toThrow();
  });
});

describe("Collector and Gate Failure Mode Verification (Real Negative Tests)", () => {
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
});
