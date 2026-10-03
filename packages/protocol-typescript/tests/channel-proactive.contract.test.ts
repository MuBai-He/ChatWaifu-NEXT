import { describe, expect, it } from "vitest";
import {
  parseChannelProactivePolicy,
  parseChannelProactivePolicyUpdate,
  parseChannelProactivePolicySnapshot,
  parseChannelProactivePreview,
  parseChannelOutboundIntentSnapshot,
  parseChannelOutboundIntentPage,
  parseChannelOutboundIntentCancelRequest,
  parseChannelDeliverySnapshot,
  parseChannelDeliveryPlanSnapshot,
  parseChannelDeliveryAcknowledgement,
} from "../src/index";

const id = "00000000-0000-4000-8000-000000000001";
const now = "2026-10-03T10:00:00+08:00";

describe("fixed-owner proactive control contracts", () => {
  it("defaults an absent policy to revision zero and disabled, with bounded defaults", () => {
    const result = parseChannelProactivePolicySnapshot({ connection_id: id });
    expect(result.revision).toBe(0);
    expect(result.policy).toMatchObject({
      enabled: false,
      source: "idle_check_in",
      timezone: "Asia/Shanghai",
      idle_minutes: 45,
      cooldown_minutes: 60,
      daily_budget: 3,
      quiet_start: "23:00",
      quiet_end: "08:00",
      ttl_minutes: 15,
    });
    expect(
      parseChannelProactivePolicyUpdate({ expected_revision: 0, policy: {} })
        .policy.enabled,
    ).toBe(false);
  });
  it.each([
    { ttl_minutes: 61 },
    { daily_budget: 0 },
    { daily_budget: 21 },
    { idle_minutes: 1.5 },
    { quiet_start: "24:00" },
    { quiet_end: "08:60" },
    { timezone: "Not/A_Timezone" },
    { timezone: "Factory" },
    { timezone: "localtime" },
    { timezone: "posixrules" },
    { timezone: "+03:00" },
    { source: "calendar" },
    { enabled: "true" },
    { idle_minutes: true },
    { idle_minutes: "45" },
    { enabled: "false" },
    { quiet_hours_enabled: 1 },
    { recipient: "other-owner" },
  ])("rejects invalid or expanded policy %j", (policy) => {
    expect(() => parseChannelProactivePolicy(policy)).toThrow();
  });
  it("rejects extra mutation fields and negative CAS while allowing snapshot extensions", () => {
    expect(() =>
      parseChannelProactivePolicyUpdate({
        policy: {},
        expected_revision: 0,
        recipient: id,
      }),
    ).toThrow();
    expect(() =>
      parseChannelOutboundIntentCancelRequest({
        expected_revision: 0,
        send_now: true,
      }),
    ).toThrow();
    expect(() =>
      parseChannelOutboundIntentCancelRequest({ expected_revision: -1 }),
    ).toThrow();
    expect(
      parseChannelProactivePolicySnapshot({
        connection_id: id,
        future_field: true,
      }),
    ).not.toHaveProperty("future_field");
  });
  it("parses a policy-only preview without treating it as generated text", () => {
    const preview = parseChannelProactivePreview({
      connection_id: id,
      policy_revision: 0,
      eligible: false,
      reason: "disabled",
      evaluated_at: now,
      reply_text: "must not surface",
    });
    expect(preview.reason).toBe("disabled");
    expect(preview).not.toHaveProperty("reply_text");
    expect(() =>
      parseChannelProactivePreview({ ...preview, reason: "send_now" }),
    ).toThrow();
    expect(() =>
      parseChannelProactivePreview({
        ...preview,
        evaluated_at: "2026-10-03T10:00:00",
      }),
    ).toThrow();
  });
  it("preserves cancellation and actual delivery as independent facts", () => {
    const parsed = parseChannelOutboundIntentSnapshot({
      ...intent(),
      status: "settled",
      settled_reason: "cancelled",
      cancel_requested_at: now,
      delivery_status: "delivered",
      provider_receipt_present: true,
    });
    expect(parsed.status).toBe("settled");
    expect(parsed.delivery_status).toBe("delivered");
    expect(parsed.provider_receipt_present).toBe(true);
    expect(() =>
      parseChannelOutboundIntentSnapshot({ ...intent(), status: "sent" }),
    ).toThrow();
  });
  it("bounds each history page and cursor", () => {
    expect(
      parseChannelOutboundIntentPage({ items: [intent()], next_cursor: "next" })
        .items,
    ).toHaveLength(1);
    expect(() =>
      parseChannelOutboundIntentPage({
        items: Array.from({ length: 51 }, intent),
      }),
    ).toThrow();
    expect(() =>
      parseChannelOutboundIntentPage({ next_cursor: "x".repeat(257) }),
    ).toThrow();
  });
});

describe("versioned inbound/outbound delivery source", () => {
  for (const [label, parse] of [
    ["snapshot", parseChannelDeliverySnapshot],
    ["plan", parseChannelDeliveryPlanSnapshot],
  ] as const) {
    it(`accepts legacy inbound and explicit outbound ${label}`, () => {
      expect(parse(delivery())).toMatchObject({
        schema_version: "1.0",
        channel_turn_id: id,
      });
      expect(
        parse({
          ...delivery(),
          schema_version: "1.1",
          channel_turn_id: null,
          outbound_intent_id: id,
        }),
      ).toMatchObject({
        schema_version: "1.1",
        channel_turn_id: null,
        outbound_intent_id: id,
      });
    });
    it.each([
      { channel_turn_id: null },
      { outbound_intent_id: id },
      { schema_version: "1.1", channel_turn_id: null },
      { schema_version: "1.1", outbound_intent_id: id },
      { schema_version: "1.1" },
      { schema_version: "2.0", channel_turn_id: null, outbound_intent_id: id },
    ])(`rejects source/version mismatch for ${label}: %j`, (source) => {
      expect(() => parse({ ...delivery(), ...source })).toThrow();
    });
  }
  it("does not expand old parent acknowledgements to outbound source", () => {
    expect(() =>
      parseChannelDeliveryAcknowledgement({
        schema_version: "1.1",
        delivery_id: id,
        channel_turn_id: null,
        outbound_intent_id: id,
        lease_id: id,
        status: "delivered",
        acknowledged_at: now,
      }),
    ).toThrow();
  });
});
function intent() {
  return {
    request_id: id,
    connection_id: id,
    binding_id: id,
    session_id: id,
    turn_id: id,
    generation_id: id,
    status: "pending",
    policy_revision: 0,
    route_revision: 1,
    revision: 0,
    not_before_at: now,
    expires_at: now,
    created_at: now,
    updated_at: now,
  };
}
function delivery() {
  return {
    schema_version: "1.0",
    delivery_id: id,
    channel_turn_id: id,
    connection_id: id,
    status: "pending",
    part_count: 1,
    created_at: now,
    updated_at: now,
  };
}
