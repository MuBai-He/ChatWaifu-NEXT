import { describe, expect, it } from "vitest";
import {
  parseChannelDeliveryPlanSnapshot,
  parseChannelGroupAudienceRequest,
  parseChannelGroupAudienceSnapshot,
  parseChannelGroupDeliveryTarget,
  parseChannelGroupRouteCreate,
  parseChannelGroupRouteMemberSnapshot,
  parseChannelGroupRoutePage,
  parseChannelGroupRouteSnapshot,
  parseChannelGroupRouteUpdate,
  parseChannelGroupTurnCancelRequest,
  parseChannelGroupTurnPage,
  parseChannelGroupTurnSnapshot,
  parseChannelParticipantLinkCreate,
  parseChannelParticipantLinkPage,
  parseChannelParticipantLinkSnapshot,
  parseChannelParticipantLinkUpdate,
} from "../src/index";

const id = "10000000-0000-4000-8000-000000000001";
const other = "10000000-0000-4000-8000-000000000002";
const now = "2026-10-03T12:00:00Z";
const hash = "a".repeat(64);
const target = () => ({
  connection_id: id,
  account_key: "10001",
  group_id: "20001",
  route_id: id,
  route_revision: 1,
  channel_turn_id: id,
  scene_id: "scene-a",
  audience_fingerprint: hash,
});
const member = (second = false) => ({
  link_id: second ? other : id,
  sender_key: second ? "30002" : "30001",
  participant_id: second ? "bob" : "alice",
  can_speak: true,
});
const route = () => ({
  route_id: id,
  connection_id: id,
  account_key: "10001",
  group_id: "20001",
  character_id: "default",
  scene_id: "scene-a",
  display_name: "Small group",
  revision: 1,
  observation_id: id,
  audience_fingerprint: hash,
  members: [member(), member(true)],
  created_at: now,
  updated_at: now,
});
const audience = () => ({
  observation_id: id,
  connection_id: id,
  connection_revision: 1,
  account_key: "10001",
  group_id: "20001",
  member_ids: ["30001", "30002"],
  member_fingerprint: hash,
  observed_at: now,
  expires_at: "2026-10-03T12:01:00Z",
});
const link = () => ({
  link_id: id,
  account_key: "10001",
  sender_key: "30001",
  participant_id: "alice",
  enabled: true,
  revision: 1,
  created_at: now,
  updated_at: now,
});
const turn = () => ({
  channel_turn_id: id,
  connection_id: id,
  external_message_id: "1",
  sender_key: "30001",
  conversation_key: "group:20001",
  principal_scope: "scene:scene-a",
  session_id: id,
  turn_id: id,
  generation_id: id,
  chat_type: "group",
  status: "accepted",
  revision: 0,
  created_at: now,
  updated_at: now,
});
const groupTurn = () => ({
  route_id: id,
  route_revision: 1,
  scene_id: "scene-a",
  participant_id: "alice",
  turn: turn(),
});
const part = () => ({
  part_id: id,
  delivery_id: id,
  ordinal: 0,
  kind: "text",
  payload: { kind: "text", text: "Group answer" },
  status: "pending",
  provider_client_id: "opaque-key",
  created_at: now,
  updated_at: now,
});
const plan = () => ({
  delivery_id: id,
  channel_turn_id: id,
  connection_id: id,
  status: "pending",
  part_count: 1,
  parts: [part()],
  group_target: target(),
  created_at: now,
  updated_at: now,
});

describe("QQ group typed contracts", () => {
  it("exposes every DTO parser and preserves closed defaults", () => {
    expect(
      parseChannelGroupAudienceRequest({ group_id: "20001" }).schema_version,
    ).toBe("1.0");
    expect(
      parseChannelGroupAudienceSnapshot(audience()).member_ids,
    ).toHaveLength(2);
    expect(
      parseChannelParticipantLinkCreate({
        observation_id: id,
        sender_key: "30001",
        participant_id: "alice",
      }).sender_key,
    ).toBe("30001");
    expect(
      parseChannelParticipantLinkUpdate({
        enabled: false,
        expected_revision: 1,
      }).enabled,
    ).toBe(false);
    expect(parseChannelParticipantLinkSnapshot(link()).provider_id).toBe(
      "qq_napcat",
    );
    expect(
      parseChannelGroupRouteCreate({
        observation_id: id,
        display_name: "Small group",
      }).speaker_sender_keys,
    ).toEqual([]);
    expect(parseChannelGroupRouteMemberSnapshot(member()).can_speak).toBe(true);
    expect(parseChannelGroupRouteSnapshot(route()).enabled).toBe(false);
    expect(parseChannelGroupRouteSnapshot(route()).allow_requested_voice).toBe(
      false,
    );
    expect(
      parseChannelGroupRouteUpdate({
        enabled: false,
        expected_revision: 1,
        speaker_sender_keys: [],
      }).allow_requested_voice,
    ).toBeUndefined();
    expect(
      parseChannelGroupRouteUpdate({
        enabled: false,
        expected_revision: 1,
        speaker_sender_keys: [],
        allow_requested_voice: true,
      }).allow_requested_voice,
    ).toBe(true);
    expect(
      parseChannelGroupRouteUpdate({
        enabled: false,
        expected_revision: 1,
        speaker_sender_keys: [],
      }).enabled,
    ).toBe(false);
    expect(
      parseChannelGroupTurnCancelRequest({ expected_revision: 0 })
        .expected_revision,
    ).toBe(0);
    expect(
      parseChannelGroupTurnSnapshot(groupTurn()).provider_receipt_present,
    ).toBe(false);
    expect(parseChannelGroupDeliveryTarget(target()).kind).toBe("group");
    for (const parse of [
      parseChannelGroupRoutePage,
      parseChannelParticipantLinkPage,
      parseChannelGroupTurnPage,
    ]) {
      expect(parse({}).items).toEqual([]);
      expect(() => parse({ next_cursor: "x".repeat(257) })).toThrow();
      expect(() => parse({ next_cursor: "" })).toThrow();
    }
    expect(parseChannelGroupRoutePage({ items: [route()] }).items).toHaveLength(
      1,
    );
    expect(
      parseChannelParticipantLinkPage({ items: [link()] }).items,
    ).toHaveLength(1);
    expect(
      parseChannelGroupTurnPage({ items: [groupTurn()] }).items,
    ).toHaveLength(1);
  });
  it.each([0, -1, 1.5, true, "1"])(
    "requires strict positive CAS revision %s",
    (revision) => {
      expect(() =>
        parseChannelParticipantLinkUpdate({
          enabled: true,
          expected_revision: revision,
        }),
      ).toThrow();
      expect(() =>
        parseChannelGroupRouteUpdate({
          enabled: false,
          expected_revision: revision,
          speaker_sender_keys: [],
        }),
      ).toThrow();
    },
  );
  it.each([false, "true", 1, null])(
    "does not coerce enabling and requires explicit observation and speaker %s",
    (enabled) => {
      if (enabled !== false)
        expect(() =>
          parseChannelGroupRouteUpdate({
            enabled,
            expected_revision: 1,
            speaker_sender_keys: [],
          }),
        ).toThrow();
      expect(() =>
        parseChannelGroupRouteUpdate({
          enabled: true,
          expected_revision: 1,
          speaker_sender_keys: [],
        }),
      ).toThrow();
      expect(() =>
        parseChannelGroupRouteUpdate({
          enabled: true,
          expected_revision: 1,
          observation_id: id,
          speaker_sender_keys: [],
        }),
      ).toThrow();
      expect(() =>
        parseChannelGroupRouteUpdate({
          enabled: true,
          expected_revision: 1,
          speaker_sender_keys: ["30001"],
        }),
      ).toThrow();
      expect(
        parseChannelGroupRouteUpdate({
          enabled: true,
          expected_revision: 1,
          observation_id: id,
          speaker_sender_keys: ["30001"],
        }).enabled,
      ).toBe(true);
    },
  );
  it.each([
    "0",
    "01",
    "-1",
    "+1",
    " 1",
    "1 ",
    "1.0",
    "x",
    "1".repeat(21),
    10001,
  ])("rejects noncanonical QQ IDs %s", (bad) => {
    expect(() => parseChannelGroupAudienceRequest({ group_id: bad })).toThrow();
    expect(() =>
      parseChannelGroupAudienceSnapshot({ ...audience(), account_key: bad }),
    ).toThrow();
    expect(() =>
      parseChannelGroupAudienceSnapshot({
        ...audience(),
        member_ids: ["30001", bad],
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupRouteUpdate({
        enabled: false,
        expected_revision: 1,
        speaker_sender_keys: [bad],
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupDeliveryTarget({ ...target(), group_id: bad }),
    ).toThrow();
  });
  it("rejects hidden input authority, duplicates, overlarge audiences and invalid dates", () => {
    expect(() =>
      parseChannelGroupAudienceRequest({ group_id: "20001", enabled: true }),
    ).toThrow();
    expect(() =>
      parseChannelParticipantLinkCreate({
        observation_id: id,
        sender_key: "30001",
        participant_id: " alice ",
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupRouteCreate({
        observation_id: id,
        display_name: " group ",
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupTurnCancelRequest({
        expected_revision: 0,
        reason: "private",
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupAudienceSnapshot({
        ...audience(),
        member_ids: ["30001", "30001"],
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupAudienceSnapshot({
        ...audience(),
        member_ids: ["30001"],
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupAudienceSnapshot({
        ...audience(),
        member_ids: Array.from({ length: 33 }, (_, i) => String(i + 1)),
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupRouteUpdate({
        enabled: false,
        expected_revision: 1,
        speaker_sender_keys: ["30001", "30001"],
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupRouteSnapshot({
        ...route(),
        members: [member(), member()],
      }),
    ).toThrow();
    for (const expires_at of [
      now,
      "2026-10-03T11:59:00Z",
      "2026-10-03T12:01:00.001Z",
      "2026-10-03T12:01:00",
      "2026-02-31T12:01:00Z",
    ])
      expect(() =>
        parseChannelGroupAudienceSnapshot({ ...audience(), expires_at }),
      ).toThrow();
    expect(
      parseChannelGroupAudienceSnapshot({
        ...audience(),
        observed_at: "2026-10-03T20:00:00+08:00",
        expires_at: "2026-10-03T12:01:00Z",
      }),
    ).toBeDefined();
    expect(() =>
      parseChannelGroupRouteSnapshot({ ...route(), pause_reason: "fresh" }),
    ).toThrow();
    expect(() =>
      parseChannelGroupTurnSnapshot({
        ...groupTurn(),
        turn: { ...turn(), status: "unknown" },
      }),
    ).toThrow();
  });
  it("bounds all page types to 50 validated records", () => {
    expect(() =>
      parseChannelGroupRoutePage({ items: Array.from({ length: 51 }, route) }),
    ).toThrow();
    expect(() =>
      parseChannelParticipantLinkPage({
        items: Array.from({ length: 51 }, link),
      }),
    ).toThrow();
    expect(() =>
      parseChannelGroupTurnPage({
        items: Array.from({ length: 51 }, groupTurn),
      }),
    ).toThrow();
  });
});

describe("fixed typed group plan", () => {
  it("validates group target recursively and still accepts legacy private/outbound plans", () => {
    expect(parseChannelDeliveryPlanSnapshot(plan()).group_target?.kind).toBe(
      "group",
    );
    expect(
      parseChannelDeliveryPlanSnapshot({ ...plan(), group_target: null })
        .group_target,
    ).toBeNull();
    expect(
      parseChannelDeliveryPlanSnapshot({
        ...plan(),
        schema_version: "1.1",
        channel_turn_id: null,
        outbound_intent_id: other,
        group_target: null,
      }),
    ).toBeDefined();
  });
  it.each([
    { group_target: { ...target(), kind: "direct" } },
    { group_target: { ...target(), connection_id: other } },
    { group_target: { ...target(), channel_turn_id: other } },
    { group_target: { ...target(), route_revision: 0 } },
    { group_target: { ...target(), audience_fingerprint: "bad" } },
    { group_target: { ...target(), audience_ids: ["private"] } },
    { schema_version: "1.1", channel_turn_id: null, outbound_intent_id: other },
    { part_count: 2 },
    { parts: [] },
    { parts: [part(), part()] },
    { parts: [{ ...part(), delivery_id: other }] },
    { parts: [{ ...part(), ordinal: 1 }] },
    { parts: [{ ...part(), required: false }] },
    { parts: [{ ...part(), kind: "audio" }] },
  ])("rejects malformed or mismatched fixed target/part %j", (change) => {
    expect(() =>
      parseChannelDeliveryPlanSnapshot({ ...plan(), ...change }),
    ).toThrow();
  });
});
