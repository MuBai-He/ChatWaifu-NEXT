import { describe, expect, it } from "vitest";

import { setStreamCaptureEnabled } from "./voiceClient";

describe("voice capture gating", () => {
  it("enables and disables every outbound audio track", () => {
    const tracks = [{ enabled: true }, { enabled: true }];
    const stream = {
      getAudioTracks: () => tracks,
    } as unknown as Pick<MediaStream, "getAudioTracks">;

    setStreamCaptureEnabled(stream, false);
    expect(tracks.map((track) => track.enabled)).toEqual([false, false]);

    setStreamCaptureEnabled(stream, true);
    expect(tracks.map((track) => track.enabled)).toEqual([true, true]);
  });

  it("tolerates capture changes before a stream exists", () => {
    expect(() => setStreamCaptureEnabled(null, false)).not.toThrow();
  });
});
