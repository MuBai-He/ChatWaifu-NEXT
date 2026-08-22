import { describe, expect, it } from "vitest";

import { SyntheticLipSyncSource, clamp01 } from "../src/lip-sync";

describe("lip sync sources", () => {
  it("produces bounded deterministic synthetic envelopes", () => {
    const left = new SyntheticLipSyncSource("random", 42);
    const right = new SyntheticLipSyncSource("random", 42);
    const samples = [0, 16, 32, 48].map((time) => left.sample(time));

    expect(samples).toEqual([0, 16, 32, 48].map((time) => right.sample(time)));
    expect(samples.every((sample) => sample >= 0 && sample <= 1)).toBe(true);
  });

  it("clamps invalid analyser and MotionSync output", () => {
    expect(clamp01(-1)).toBe(0);
    expect(clamp01(2)).toBe(1);
    expect(clamp01(Number.NaN)).toBe(0);
  });
});
