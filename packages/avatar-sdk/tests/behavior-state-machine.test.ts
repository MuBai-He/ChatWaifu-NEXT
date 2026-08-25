import { describe, expect, it } from "vitest";

import {
  AvatarBehaviorStateMachine,
  type AvatarBehaviorInput,
} from "../src/behavior-state-machine";

const IDLE: AvatarBehaviorInput = {
  state: "idle",
  expression: "neutral",
  gaze: "center",
  speaking: false,
  interrupted: false,
  speechEnergy: 0,
};

describe("AvatarBehaviorStateMachine", () => {
  it("produces deterministic bounded micro-motion for a fixed seed", () => {
    const first = new AvatarBehaviorStateMachine(42);
    const second = new AvatarBehaviorStateMachine(42);

    for (let nowMs = 0; nowMs <= 6_000; nowMs += 16) {
      const firstFrame = first.step(IDLE, nowMs);
      const secondFrame = second.step(IDLE, nowMs);
      expect(firstFrame).toEqual(secondFrame);
      for (const [channel, value] of Object.entries(firstFrame)) {
        if (channel === "mode") continue;
        expect(Number.isFinite(value)).toBe(true);
        expect(value).toBeGreaterThanOrEqual(channel === "eyeOpen" ? 0 : -1);
        expect(value).toBeLessThanOrEqual(1);
      }
    }
  });

  it("moves eyes before the slower head when entering thinking", () => {
    const behavior = new AvatarBehaviorStateMachine(7);
    const thinking = { ...IDLE, state: "thinking" };
    behavior.step(thinking, 0);

    const frame = behavior.step(thinking, 16);

    expect(frame.mode).toBe("thinking");
    expect(Math.abs(frame.eyeX)).toBeGreaterThan(Math.abs(frame.headYaw));
  });

  it("uses speech energy for subtle speaking motion and neutralizes on interruption", () => {
    const behavior = new AvatarBehaviorStateMachine(11);
    const speaking = {
      ...IDLE,
      state: "speaking",
      speaking: true,
      speechEnergy: 1,
    };
    behavior.step(speaking, 0);
    let speakingFrame = behavior.step(speaking, 16);
    for (let nowMs = 32; nowMs <= 480; nowMs += 16) {
      speakingFrame = behavior.step(speaking, nowMs);
    }
    expect(Math.abs(speakingFrame.headPitch)).toBeGreaterThan(0.005);

    const interrupted = { ...speaking, speaking: false, interrupted: true };
    const before = Math.abs(speakingFrame.headPitch);
    let interruptedFrame = behavior.step(interrupted, 496);
    for (let nowMs = 512; nowMs <= 800; nowMs += 16) {
      interruptedFrame = behavior.step(interrupted, nowMs);
    }

    expect(interruptedFrame.mode).toBe("interrupted");
    expect(Math.abs(interruptedFrame.headPitch)).toBeLessThan(before);
    expect(interruptedFrame.eyeOpen).toBeGreaterThan(0.95);
  });

  it("resets event timing and springs to a reproducible initial state", () => {
    const behavior = new AvatarBehaviorStateMachine(99);
    const first = [
      behavior.step(IDLE, 0),
      behavior.step(IDLE, 16),
      behavior.step(IDLE, 32),
    ];
    behavior.step({ ...IDLE, state: "thinking" }, 2_000);
    behavior.reset();
    const replay = [
      behavior.step(IDLE, 0),
      behavior.step(IDLE, 16),
      behavior.step(IDLE, 32),
    ];

    expect(replay).toEqual(first);
  });
});
