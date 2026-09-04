import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  decodeAudioFrameHeader,
  encodeAudioFrameHeader,
  parseAudioFrameHeader,
  parseAvatarCue,
  parseAvatarInteractionEvent,
  parseChannelAuthorizationSnapshot,
  parseChannelAuthorizationStartRequest,
  parseChannelAuthorizationVerificationRequest,
  parseChannelConnectionSnapshot,
  parseChannelDeliveryAcknowledgement,
  parseChannelDeliveryPartAcknowledgement,
  parseChannelDeliveryClaimRequest,
  parseChannelInboundTextMessage,
  parseChannelTurnSnapshot,
  parseCommandEnvelope,
  parseEventEnvelope,
  parseMcpCapabilitySnapshot,
  parseMemoryChannelAttribution,
  parseMemorySource,
  parseSessionSnapshot,
  parseSkillRunSnapshot,
} from "../src/index";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const fixtureRoot = path.join(root, "tests/fixtures/protocol/v1");
const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(path.join(fixtureRoot, name), "utf8"));

describe("cross-language protocol fixtures", () => {
  it("parses the Python event fixture with runtime validation", () => {
    const event = parseEventEnvelope(
      fixture("python-session-created-event.json"),
    );
    expect(event.event_type).toBe("session.created");
    expect(event.payload.character_id).toBe("default-character");
  });

  it("parses every Python-owned generic core event type", () => {
    const event = fixture("python-session-created-event.json") as Record<
      string,
      unknown
    >;
    const eventTypes = fixture(
      "python-generic-core-event-types.json",
    ) as string[];
    const specializedPayloads: Record<string, Record<string, unknown>> = {
      "session.data_reset": {
        character_id: "default-character",
        user_scope: "local",
        conversation: "current_session",
        audio: "current_session",
        memory: "current_character_user",
        character_state: "current_character_user",
      },
      "assistant.text_delta": { text: "你" },
      "assistant.generation_cancelled": { reason: "interrupted" },
      "assistant.generation_completed": {
        text: "你好",
        assistant_turn_id: "00000000-0000-4000-8000-000000000302",
      },
      "conversation.interrupted": { reason: "interrupted" },
    };
    for (const eventType of eventTypes) {
      expect(
        parseEventEnvelope({
          ...event,
          event_type: eventType,
          payload: specializedPayloads[eventType] ?? {},
        }).event_type,
      ).toBe(eventType);
    }
  });

  it("rejects malformed high-value realtime and reset payloads", () => {
    const event = fixture("python-session-created-event.json") as Record<
      string,
      unknown
    >;
    for (const eventType of [
      "session.data_reset",
      "assistant.text_delta",
      "assistant.generation_completed",
      "assistant.generation_cancelled",
      "conversation.interrupted",
    ]) {
      expect(() =>
        parseEventEnvelope({ ...event, event_type: eventType, payload: {} }),
      ).toThrow();
    }
  });

  it("parses the TypeScript command fixture", () => {
    const command = parseCommandEnvelope(
      fixture("typescript-text-send-command.json"),
    );
    expect(command.command_type).toBe("cmd.text.send");
    expect(command.payload.text).toBe("你好，Hikari");
  });

  it("round-trips a binary audio header without the binary body", () => {
    const header = parseAudioFrameHeader(fixture("audio-frame-header.json"));
    expect(header.codec).toBe("pcm_s16le");
    const encoded = encodeAudioFrameHeader(header);
    expect(encoded).toBeInstanceOf(Uint8Array);
    expect(decodeAudioFrameHeader(encoded)).toEqual(header);
  });

  it("rejects an unknown schema major and invalid payload", () => {
    const event = fixture("python-session-created-event.json") as Record<
      string,
      unknown
    >;
    expect(() =>
      parseEventEnvelope({ ...event, schema_version: "2.0" }),
    ).toThrow();
    expect(() => parseEventEnvelope({ ...event, payload: {} })).toThrow();
  });

  it("accepts forward-compatible optional fields in the same major", () => {
    const event = fixture("python-session-created-event.json") as Record<
      string,
      unknown
    >;
    expect(
      parseEventEnvelope({
        ...event,
        schema_version: "1.9",
        future_hint: true,
      }),
    ).toBeTruthy();
  });

  it("validates specialized playback events and rejects undeclared types", () => {
    const event = fixture("python-session-created-event.json") as Record<
      string,
      unknown
    >;
    expect(
      parseEventEnvelope({
        ...event,
        event_type: "assistant.playback_stopped",
        generation_id: "00000000-0000-4000-8000-000000000301",
        payload: {
          stream_id: "00000000-0000-4000-8000-000000000401",
          segment_id: "00000000-0000-4000-8000-000000000402",
          played_pts_ms: 1840,
          buffered_ms: 0,
          client_clock_ms: 12040,
          transport: "audio_element",
          reason: "ended",
          completed: true,
        },
      }).event_type,
    ).toBe("assistant.playback_stopped");
    expect(() =>
      parseEventEnvelope({
        ...event,
        event_type: "assistant.playback_progress",
        payload: { played_pts_ms: 100 },
      }),
    ).toThrow();
    expect(() =>
      parseEventEnvelope({ ...event, event_type: "assistant.future_event" }),
    ).toThrow();
  });

  it("validates browser playback acknowledgement commands", () => {
    const command = fixture("typescript-text-send-command.json") as Record<
      string,
      unknown
    >;
    const parsed = parseCommandEnvelope({
      ...command,
      command_type: "cmd.playback.ack",
      generation_id: "00000000-0000-4000-8000-000000000301",
      payload: {
        phase: "progress",
        stream_id: "00000000-0000-4000-8000-000000000401",
        segment_id: "00000000-0000-4000-8000-000000000402",
        played_pts_ms: 640,
        buffered_ms: 220,
        client_clock_ms: 10840,
        transport: "webrtc",
      },
    });
    expect(parsed.command_type).toBe("cmd.playback.ack");
  });

  it("validates avatar cues and semantic interaction events", () => {
    expect(
      parseAvatarCue({
        cue_id: "00000000-0000-4000-8000-000000000701",
        kind: "motion",
        name: "nod",
        intensity: 0.8,
      }).name,
    ).toBe("nod");
    expect(() =>
      parseAvatarCue({
        cue_id: "00000000-0000-4000-8000-000000000702",
        kind: "motion",
        name: "nod",
        intensity: 2,
      }),
    ).toThrow();
    expect(
      parseAvatarInteractionEvent({
        interaction_id: "00000000-0000-4000-8000-000000000703",
        avatar_id: "avatar-lab",
        kind: "touch",
        target: "touched_head",
      }).target,
    ).toBe("touched_head");
  });

  it("validates generated HTTP control-plane snapshots", () => {
    const session = parseSessionSnapshot({
      session_id: "00000000-0000-4000-8000-000000000801",
      character_id: "nene",
      state: "ready",
      conversation_state: "idle",
      revision: 2,
      created_at: "2026-08-29T00:00:00Z",
      updated_at: "2026-08-29T00:00:01Z",
    });
    expect(session.state).toBe("ready");
    expect(() => parseSessionSnapshot({ ...session, revision: -1 })).toThrow();

    const capabilities = parseMcpCapabilitySnapshot({
      connection_id: "00000000-0000-4000-8000-000000000802",
    });
    expect(capabilities.tools).toEqual([]);
    expect(() =>
      parseMcpCapabilitySnapshot({ connection_id: "not-a-uuid" }),
    ).toThrow();
  });

  it("validates Runtime Skill lineage at the TypeScript boundary", () => {
    const run = parseSkillRunSnapshot({
      skill_run_id: "00000000-0000-4000-8000-000000000901",
      session_id: "00000000-0000-4000-8000-000000000902",
      turn_id: "00000000-0000-4000-8000-000000000903",
      generation_id: "00000000-0000-4000-8000-000000000904",
      origin: "agent",
      provider_tool_call_id: "call_weather",
      skill_id: "weather.search",
      skill_version: "1.0.0",
      capability: "lookup",
      state: "running",
      created_at: "2026-08-29T00:00:00Z",
      updated_at: "2026-08-29T00:00:01Z",
    });
    expect(run.origin).toBe("agent");
    expect(run.provider_tool_call_id).toBe("call_weather");
    expect(() => parseSkillRunSnapshot({ ...run, origin: "model" })).toThrow();
    expect(() =>
      parseSkillRunSnapshot({ ...run, turn_id: "not-a-uuid" }),
    ).toThrow();
  });

  it("preserves versioned channel attribution on memory sources", () => {
    const channelAttribution = parseMemoryChannelAttribution({
      schema_version: "1.0",
      provider_id: "weixin_ilink",
      connection_id: "00000000-0000-4000-8000-000000000951",
      account_key: "wechat-owner-account",
      principal_scope: "local",
      chat_type: "direct",
      conversation_key: "wechat-direct-owner",
      sender_key: "wechat-owner-sender",
      received_at: "2026-08-31T08:00:00Z",
      conversation_label: "与木白的微信私聊",
      sender_display_name: "木白",
    });
    const source = parseMemorySource({
      source_id: "00000000-0000-4000-8000-000000000952",
      memory_id: "00000000-0000-4000-8000-000000000953",
      source_event_id: "00000000-0000-4000-8000-000000000954",
      session_id: "00000000-0000-4000-8000-000000000955",
      turn_id: "00000000-0000-4000-8000-000000000956",
      source_kind: "user_turn",
      created_at: "2026-08-31T08:00:01Z",
      channel_attribution: channelAttribution,
    });

    expect(source.channel_attribution?.provider_id).toBe("weixin_ilink");
    expect(source.channel_attribution?.received_at).toBe(
      "2026-08-31T08:00:00Z",
    );
    expect(() =>
      parseMemoryChannelAttribution({
        ...channelAttribution,
        schema_version: "2.0",
      }),
    ).toThrow();
  });

  it("parses Python-owned external channel identity without provider state", () => {
    const message = parseChannelInboundTextMessage(
      fixture("python-channel-inbound-text-message.json"),
    );
    expect(message.account_key).toBe("provider-account-001");
    expect(message.external_message_id).toBe("provider-message-001");
    expect(message.conversation_key).toBe("provider-direct-conversation-001");
    expect(message.sender_key).toBe("provider-sender-001");
    expect(message.principal_scope).toBe("owner/local");
    expect(message.conversation_label).toBe("与宁宁的测试会话");
    expect(message.sender_display_name).toBe("木白");
    expect(message.chat_type).toBe("direct");
    expect(message.kind).toBe("text");
    expect(
      parseChannelInboundTextMessage({ ...message, chat_type: "group" })
        .chat_type,
    ).toBe("group");
    expect(() =>
      parseChannelInboundTextMessage({ ...message, kind: "image" }),
    ).toThrow();
    expect(() =>
      parseChannelInboundTextMessage({ ...message, schema_version: "1.1" }),
    ).toThrow();
  });

  it("validates provider-neutral channel authorization without credentials", () => {
    const request = parseChannelAuthorizationStartRequest({
      schema_version: "1.0",
      provider_id: "weixin_ilink",
      character_id: "ayachi_nene",
    });
    expect(request.method).toBe("qr_code");
    expect(request.principal_scope).toBe("local");

    const verification = parseChannelAuthorizationVerificationRequest({
      verification_code: "271828",
    });
    expect(verification.verification_code).toBe("271828");
    expect(() =>
      parseChannelAuthorizationVerificationRequest({
        verification_code: "code with spaces",
      }),
    ).toThrow();

    const now = "2026-08-31T08:00:00Z";
    const pending = parseChannelAuthorizationSnapshot({
      schema_version: "1.0",
      auth_session_id: "00000000-0000-4000-8000-000000000b01",
      provider_id: "weixin_ilink",
      status: "pending",
      qr_code_content: "https://example.invalid/opaque-qr-content",
      expires_at: now,
      created_at: now,
      updated_at: now,
    });
    expect(pending.verification_required).toBe(false);
    expect(() =>
      parseChannelAuthorizationSnapshot({
        ...pending,
        status: "verification_required",
        verification_required: false,
      }),
    ).toThrow();
    expect(() =>
      parseChannelAuthorizationSnapshot({
        ...pending,
        status: "confirmed",
        qr_code_content: null,
      }),
    ).toThrow();
  });

  it("parses TypeScript-owned delivery acknowledgements in both languages", () => {
    const acknowledgement = parseChannelDeliveryAcknowledgement(
      fixture("typescript-channel-delivery-ack.json"),
    );
    expect(acknowledgement.status).toBe("delivered");
    expect(acknowledgement.lease_id).toBe(
      "00000000-0000-4000-8000-000000000903",
    );
    expect(acknowledgement.provider_message_id).toBe("provider-reply-001");
    expect(() =>
      parseChannelDeliveryAcknowledgement({
        ...acknowledgement,
        status: "failed",
      }),
    ).toThrow();
  });

  it("requires a bounded delivery lease before provider send", () => {
    const claim = parseChannelDeliveryClaimRequest({
      schema_version: "1.0",
      delivery_id: "00000000-0000-4000-8000-000000000901",
      channel_turn_id: "00000000-0000-4000-8000-000000000902",
      lease_id: "00000000-0000-4000-8000-000000000903",
      lease_seconds: 60,
    });
    expect(claim.lease_seconds).toBe(60);
    expect(() =>
      parseChannelDeliveryClaimRequest({ ...claim, lease_seconds: 301 }),
    ).toThrow();
  });

  it("validates channel connection revisions and durable delivery state", () => {
    const connectionId = "00000000-0000-4000-8000-000000000a01";
    const now = "2026-08-29T00:00:00Z";
    const connection = parseChannelConnectionSnapshot({
      schema_version: "1.0",
      configuration: {
        schema_version: "1.0",
        connection_id: connectionId,
        provider_id: "example_direct",
        name: "External direct channel",
        character_id: "nene",
        principal_scope: "owner/local",
        account_key: "provider-account-001",
        allowed_sender_keys: ["provider-sender-001"],
      },
      revision: 1,
      status: "ready",
      created_at: now,
      updated_at: now,
    });
    expect(connection.revision).toBe(1);
    expect(connection.configuration.allowed_sender_keys).toEqual([
      "provider-sender-001",
    ]);
    expect(connection.capabilities.chat_types).toEqual(["direct"]);
    expect(() =>
      parseChannelConnectionSnapshot({ ...connection, revision: 0 }),
    ).toThrow();

    const turn = parseChannelTurnSnapshot({
      schema_version: "1.0",
      channel_turn_id: "00000000-0000-4000-8000-000000000a02",
      connection_id: connectionId,
      account_key: "provider-account-001",
      external_message_id: "provider-message-001",
      conversation_key: "provider-direct-conversation-001",
      sender_key: "provider-sender-001",
      principal_scope: "owner/local",
      chat_type: "direct",
      conversation_label: "与宁宁的测试会话",
      sender_display_name: "木白",
      session_id: "00000000-0000-4000-8000-000000000a03",
      turn_id: "00000000-0000-4000-8000-000000000a04",
      generation_id: "00000000-0000-4000-8000-000000000a05",
      status: "completed",
      reply_text: "今天也请多关照。",
      delivery_id: "00000000-0000-4000-8000-000000000a06",
      delivery_status: "delivered",
      revision: 3,
      created_at: now,
      updated_at: now,
      completed_at: now,
    });
    expect(turn.delivery_status).toBe("delivered");
  });

  it("enforces ChannelDeliveryPartAcknowledgement status cannot be cancelled", () => {
    const validDelivered = {
      schema_version: "1.0",
      delivery_id: "00000000-0000-4000-8000-000000000a01",
      part_id: "00000000-0000-4000-8000-000000000a02",
      lease_id: "00000000-0000-4000-8000-000000000a03",
      status: "delivered",
      acknowledged_at: "2026-09-04T00:00:00Z",
    };
    const parsedDelivered =
      parseChannelDeliveryPartAcknowledgement(validDelivered);
    expect(parsedDelivered.status).toBe("delivered");

    const validFailed = {
      schema_version: "1.0",
      delivery_id: "00000000-0000-4000-8000-000000000a01",
      part_id: "00000000-0000-4000-8000-000000000a02",
      lease_id: "00000000-0000-4000-8000-000000000a03",
      status: "failed",
      error: {
        code: "test_error",
        message: "delivery failed",
        retryable: false,
        component: "external_channels",
      },
      acknowledged_at: "2026-09-04T00:00:00Z",
    };
    const parsedFailed = parseChannelDeliveryPartAcknowledgement(validFailed);
    expect(parsedFailed.status).toBe("failed");

    const invalidCancelled = {
      schema_version: "1.0",
      delivery_id: "00000000-0000-4000-8000-000000000a01",
      part_id: "00000000-0000-4000-8000-000000000a02",
      lease_id: "00000000-0000-4000-8000-000000000a03",
      status: "cancelled",
      acknowledged_at: "2026-09-04T00:00:00Z",
    };
    expect(() =>
      parseChannelDeliveryPartAcknowledgement(invalidCancelled),
    ).toThrow();
  });

  it("keeps cancelled only for legacy whole-delivery acknowledgements", () => {
    const cancelled = {
      schema_version: "1.0",
      delivery_id: "00000000-0000-4000-8000-000000000a01",
      channel_turn_id: "00000000-0000-4000-8000-000000000a02",
      lease_id: "00000000-0000-4000-8000-000000000a03",
      status: "cancelled",
      acknowledged_at: "2026-09-04T00:00:00Z",
    };

    expect(() => parseChannelDeliveryAcknowledgement(cancelled)).not.toThrow();

    expect(() =>
      parseChannelDeliveryPartAcknowledgement({
        ...cancelled,
        part_id: "00000000-0000-4000-8000-000000000a04",
      }),
    ).toThrow();
  });
});
