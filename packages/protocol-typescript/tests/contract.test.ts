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
  parseCommandEnvelope,
  parseEventEnvelope,
  parseMcpCapabilitySnapshot,
  parseSessionSnapshot,
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
});
