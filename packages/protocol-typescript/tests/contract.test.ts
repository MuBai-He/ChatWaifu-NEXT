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

  it("accepts declared generic core events and rejects undeclared types", () => {
    const event = fixture("python-session-created-event.json") as Record<
      string,
      unknown
    >;
    expect(
      parseEventEnvelope({
        ...event,
        event_type: "assistant.playback_stopped",
        payload: { reason: "completed" },
      }).event_type,
    ).toBe("assistant.playback_stopped");
    expect(() =>
      parseEventEnvelope({ ...event, event_type: "assistant.future_event" }),
    ).toThrow();
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
});
