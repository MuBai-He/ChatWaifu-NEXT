import { describe, expect, it } from "vitest";
import { AiriMotionDriver } from "../src/experimental/airi-motion";
import { neutralProceduralFrame } from "../src/behavior-state-machine";

const base = {
  ...neutralProceduralFrame(),
  eyeX: 0.3,
  eyeOpen: 0.7,
  mouthForm: -0.2,
  breath: 0.4,
};
const axes = [
  "headYaw",
  "headPitch",
  "headRoll",
  "bodyYaw",
  "bodyPitch",
  "bodyRoll",
] as const;

describe("AIRI motion experiment", () => {
  it.each(["idle-calm", "speaking-excited"] as const)(
    "keeps %s finite and bounded over five minutes and preserves face channels",
    (profile) => {
      const driver = new AiriMotionDriver({ profile, intensity: 1 });
      let range = 0;
      for (let time = 0; time <= 300000; time += 1000 / 60) {
        const frame = driver.apply(base, time, false);
        for (const axis of axes) {
          expect(Number.isFinite(frame[axis])).toBe(true);
          expect(Math.abs(frame[axis])).toBeLessThanOrEqual(1);
          range = Math.max(range, Math.abs(frame[axis]));
        }
        expect(frame.eyeX).toBe(base.eyeX);
        expect(frame.eyeOpen).toBe(base.eyeOpen);
        expect(frame.mouthForm).toBe(base.mouthForm);
        expect(frame.breath).toBe(base.breath);
      }
      expect(range).toBeGreaterThan(0.03);
    },
  );

  it("replays the seeded output after reset", () => {
    const driver = new AiriMotionDriver({
      profile: "idle-calm",
      intensity: 0.65,
    });
    const run = () =>
      Array.from({ length: 180 }, (_, i) =>
        driver.apply(base, (i * 1000) / 60, false),
      );
    const first = run();
    driver.reset();
    expect(run()).toEqual(first);
  });

  it("generates the same pose at the same elapsed time at 30 and 60 FPS", () => {
    const simulate = (fps: number) => {
      const driver = new AiriMotionDriver({
        profile: "speaking-excited",
        intensity: 1,
      });
      let pose = base;
      for (let frame = 0; frame <= fps * 10; frame++)
        pose = driver.apply(base, (frame * 1000) / fps, false);
      return pose;
    };
    const low = simulate(30),
      high = simulate(60);
    for (const axis of axes) expect(low[axis]).toBeCloseTo(high[axis], 5);
  });

  it("yields immediately to gestures and interrupt, resumes smoothly and bounds background catchup", () => {
    const driver = new AiriMotionDriver({
      profile: "speaking-excited",
      intensity: 1,
    });
    for (let time = 0; time <= 5000; time += 20)
      driver.apply(base, time, false);
    expect(driver.apply(base, 5020, true)).toEqual(base);
    expect(driver.apply(base, 1000000, true)).toEqual(base);
    const resumed = driver.apply(base, 1000016, false);
    for (const axis of axes)
      expect(Math.abs(resumed[axis] - base[axis])).toBeLessThan(0.1);
    const afterPause = driver.apply(base, 1000000000, false);
    for (const axis of axes)
      expect(Number.isFinite(afterPause[axis])).toBe(true);
    driver.configure({ profile: "idle-calm", intensity: 0 });
    let stopped = afterPause;
    for (let frame = 1; frame < 200; frame++)
      stopped = driver.apply(base, 1000000000 + frame * 20, false);
    expect(stopped).toEqual(base);
  });
});
