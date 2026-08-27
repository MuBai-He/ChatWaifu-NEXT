import { describe, expect, it } from "vitest";

import type { AudioPlaybackItem } from "./audioPlayer";
import type { PlaybackAckReceipt } from "./runtimeClient";
import {
  countSubtitleTextUnits,
  normalizeDesktopSubtitle,
  SubtitlePlaybackTracker,
} from "./subtitlePlayback";

describe("desktop subtitle playback", () => {
  it("folds paragraph gaps only in the compact subtitle projection", () => {
    expect(
      normalizeDesktopSubtitle("第一段。\n\n  \n第二段。\r\n第三段。"),
    ).toBe("第一段。\n第二段。\n第三段。");
    expect(countSubtitleTextUnits("宁宁……\n你好")).toBe(6);
  });

  it("derives cumulative text progress from actual segment playback", () => {
    const tracker = new SubtitlePlaybackTracker();
    tracker.start("generation-1");
    tracker.registerSegment(segment(0, "甲乙丙丁"));
    tracker.registerSegment(segment(1, "戊己庚辛"));

    expect(tracker.report(receipt(0, "progress", 500))).toMatchObject({
      playedTextUnits: 2,
      phase: "playing",
    });
    expect(tracker.report(receipt(0, "stopped", 1000))).toMatchObject({
      playedTextUnits: 4,
      phase: "stopped",
    });
    expect(tracker.report(receipt(1, "progress", 500))).toMatchObject({
      playedTextUnits: 6,
      segmentIndex: 1,
    });
  });

  it("replays a bounded out-of-order receipt when metadata arrives", () => {
    const tracker = new SubtitlePlaybackTracker();
    tracker.start("generation-1");

    expect(tracker.report(receipt(0, "progress", 750))).toBeNull();
    expect(tracker.registerSegment(segment(0, "一二三四"))).toMatchObject({
      playedTextUnits: 3,
    });
  });

  it("keeps a newer playing segment active when an older stop arrives late", () => {
    const tracker = new SubtitlePlaybackTracker();
    tracker.start("generation-1");
    tracker.registerSegment(segment(0, "甲乙丙丁"));
    tracker.registerSegment(segment(1, "戊己庚辛"));
    tracker.report(receipt(1, "progress", 500));

    expect(tracker.report(receipt(0, "stopped", 1000))).toMatchObject({
      playedTextUnits: 6,
      segmentIndex: 1,
      phase: "playing",
    });
  });

  it("does not advance for cleared or stale-generation audio", () => {
    const tracker = new SubtitlePlaybackTracker();
    tracker.start("generation-2");
    tracker.registerSegment(segment(0, "一二三四", "generation-2"));

    expect(
      tracker.report(receipt(0, "queue_cleared", 1000, "generation-2")),
    ).toBeNull();
    expect(
      tracker.report(receipt(0, "progress", 1000, "generation-1")),
    ).toBeNull();
  });
});

function segment(
  segmentIndex: number,
  text: string,
  generationId = "generation-1",
): AudioPlaybackItem {
  return {
    generationId,
    streamId: "stream-1",
    segmentId: `segment-${segmentIndex}`,
    segmentIndex,
    text,
    durationMs: 1000,
    url: `/audio/${segmentIndex}.wav`,
  };
}

function receipt(
  segmentIndex: number,
  phase: PlaybackAckReceipt["phase"],
  playedPtsMs: number,
  generationId = "generation-1",
): PlaybackAckReceipt {
  return {
    generationId,
    streamId: "stream-1",
    segmentId: `segment-${segmentIndex}`,
    playedPtsMs,
    bufferedMs: 0,
    clientClockMs: playedPtsMs,
    phase,
    transport: "audio_element",
  };
}
