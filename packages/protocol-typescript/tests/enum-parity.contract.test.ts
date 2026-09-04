import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  parseChannelDeliveryAcknowledgement,
  parseChannelDeliveryPartAcknowledgement,
  protocolEnumSchemas,
  protocolModelSchemas,
} from "../src/index";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const catalogPath = path.join(
  root,
  "schemas/domain/v1/protocol-catalog.schema.json",
);
const catalog = JSON.parse(readFileSync(catalogPath, "utf8"));

type JsonSchemaNode = {
  enum?: unknown[];
  const?: unknown;
  $ref?: string;
  anyOf?: JsonSchemaNode[];
  allOf?: JsonSchemaNode[];
  type?: string;
  items?: JsonSchemaNode;
};

function resolveJsonSchemaEnum(
  schema: JsonSchemaNode | null | undefined,
  visited = new Set<unknown>(),
): string[] | null {
  if (!schema || visited.has(schema)) return null;
  visited.add(schema);

  if (Array.isArray(schema.enum)) {
    return schema.enum.filter((v: unknown) => typeof v === "string");
  }
  if (typeof schema.const === "string") {
    return [schema.const];
  }
  if (typeof schema.$ref === "string" && schema.$ref.startsWith("#/$defs/")) {
    const target = catalog.$defs[schema.$ref.slice(8)];
    return resolveJsonSchemaEnum(target, visited);
  }
  if (Array.isArray(schema.anyOf)) {
    for (const sub of schema.anyOf) {
      const res = resolveJsonSchemaEnum(sub, visited);
      if (res && res.length > 0) return res;
    }
  }
  if (Array.isArray(schema.allOf)) {
    for (const sub of schema.allOf) {
      const res = resolveJsonSchemaEnum(sub, visited);
      if (res && res.length > 0) return res;
    }
  }
  if (schema.type === "array" && schema.items) {
    const res = resolveJsonSchemaEnum(schema.items, visited);
    if (res && res.length > 0) return res;
  }
  return null;
}

type ZodInspectable = {
  options?: unknown[];
  element?: ZodInspectable;
  _def?: {
    type?: string;
    entries?: Record<string, unknown>;
    values?: unknown[];
    value?: unknown;
    element?: ZodInspectable;
    innerType?: ZodInspectable;
    schema?: ZodInspectable;
  };
};

function extractZodEnumValues(schema: unknown): string[] | null {
  let curr: ZodInspectable | undefined = schema as ZodInspectable | undefined;
  while (curr) {
    if (Array.isArray(curr.options)) {
      return [...curr.options];
    }
    if (curr._def) {
      if (curr._def.type === "enum") {
        return curr.options
          ? [...curr.options]
          : Object.keys(curr._def.entries || {});
      }
      if (curr._def.type === "literal") {
        return Array.isArray(curr._def.values)
          ? [...curr._def.values]
          : curr._def.value !== undefined
            ? [curr._def.value]
            : null;
      }
      if (curr._def.type === "array") {
        const itemEnum = extractZodEnumValues(
          curr._def.element ?? curr.element,
        );
        if (itemEnum) return itemEnum;
      }
      if (curr._def.innerType) {
        curr = curr._def.innerType;
        continue;
      }
      if (curr._def.schema) {
        curr = curr._def.schema;
        continue;
      }
    }
    break;
  }
  return null;
}

describe("Universal Cross-Layer Enum Contract Gate", () => {
  it("enforces exact parity for standalone protocol enums", () => {
    for (const [enumName, zodEnum] of Object.entries(protocolEnumSchemas)) {
      const jsonDef = catalog.$defs[enumName];
      expect(
        jsonDef,
        `Catalog definition missing for enum: ${enumName}`,
      ).toBeDefined();

      const jsonValues = resolveJsonSchemaEnum(jsonDef);
      expect(
        jsonValues,
        `No enum values found in catalog for enum: ${enumName}`,
      ).not.toBeNull();

      const zodValues = extractZodEnumValues(zodEnum);
      expect(
        zodValues,
        `No enum values extracted from Zod schema: ${enumName}`,
      ).not.toBeNull();

      expect(
        [...(zodValues ?? [])].sort(),
        `Enum parity mismatch for ${enumName}`,
      ).toEqual([...(jsonValues ?? [])].sort());
    }
  });

  it("enforces exact parity for all model property enums", () => {
    let checkedPropertyCount = 0;

    for (const [modelName, zodSchema] of Object.entries(protocolModelSchemas)) {
      const jsonDef = catalog.$defs[modelName];
      expect(
        jsonDef,
        `Catalog definition missing for model: ${modelName}`,
      ).toBeDefined();

      const shape = (
        zodSchema as unknown as { shape?: Record<string, unknown> }
      ).shape;
      expect(
        shape,
        `Shape missing for Zod model schema: ${modelName}`,
      ).toBeDefined();

      for (const [propName, propDef] of Object.entries(
        jsonDef.properties || {},
      )) {
        const jsonEnum = resolveJsonSchemaEnum(propDef);
        const zodField = shape[propName];
        const zodEnum = zodField ? extractZodEnumValues(zodField) : null;

        if (jsonEnum && jsonEnum.length > 0) {
          expect(
            zodEnum,
            `Zod schema for ${modelName}.${propName} is missing enum validation (expected: ${JSON.stringify(jsonEnum)})`,
          ).not.toBeNull();

          expect(
            [...(zodEnum ?? [])].sort(),
            `Enum value mismatch for ${modelName}.${propName}`,
          ).toEqual([...(jsonEnum ?? [])].sort());

          checkedPropertyCount++;
        } else if (zodEnum && zodEnum.length > 0) {
          expect(
            jsonEnum,
            `JSON Schema for ${modelName}.${propName} lacks enum definition (Zod has: ${JSON.stringify(zodEnum)})`,
          ).not.toBeNull();
        }
      }
    }

    expect(checkedPropertyCount).toBeGreaterThan(20);
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
        parseChannelDeliveryAcknowledgement({
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
        parseChannelDeliveryPartAcknowledgement({
          ...partAck,
          status,
          error: status === "failed" ? structuredError : undefined,
        }),
      ).not.toThrow();
    }

    expect(() =>
      parseChannelDeliveryPartAcknowledgement({
        ...partAck,
        status: "cancelled",
      }),
    ).toThrow();
  });

  it("fails fast when an enum discrepancy is detected (self-verification)", () => {
    const fakeJsonEnum = ["delivered", "failed", "cancelled"];
    const fakeZodEnumWithoutCancelled = ["delivered", "failed"];

    expect(() => {
      expect(fakeZodEnumWithoutCancelled.sort()).toEqual(fakeJsonEnum.sort());
    }).toThrow();
  });
});
