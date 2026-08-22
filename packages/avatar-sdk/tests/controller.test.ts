import { describe, expect, it } from "vitest";

import type { AnimationClock } from "../src/audio-clock";
import { AvatarController } from "../src/controller";
import { AVATAR_LAB_MANIFEST } from "../src/default-manifest";
import { FakeAvatarRenderer } from "../src/fake-avatar-renderer";
import { SyntheticLipSyncSource } from "../src/lip-sync";
import { cue } from "./fixtures";

class ManualAnimationClock implements AnimationClock {
  private time = 0;
  private handle = 0;
  private readonly callbacks = new Map<number, FrameRequestCallback>();

  now(): number {
    return this.time;
  }

  requestFrame(callback: FrameRequestCallback): number {
    const handle = ++this.handle;
    this.callbacks.set(handle, callback);
    return handle;
  }

  cancelFrame(handle: number): void {
    this.callbacks.delete(handle);
  }

  step(time: number): void {
    this.time = time;
    const callbacks = [...this.callbacks.values()];
    this.callbacks.clear();
    for (const callback of callbacks) callback(time);
  }
}

describe("AvatarController", () => {
  it("keeps its render loop outside React and derives semantic state", async () => {
    const clock = new ManualAnimationClock();
    const renderer = new FakeAvatarRenderer();
    const controller = new AvatarController(renderer, AVATAR_LAB_MANIFEST, {
      clock,
    });
    await controller.load();
    controller.applyCue(cue("state", "speaking"));
    controller.applyCue(cue("speech", "speaking"));
    controller.setLipSyncSource(new SyntheticLipSyncSource("sine"));

    clock.step(200);

    expect(renderer.getLastState()?.state).toBe("speaking");
    expect(renderer.getLastState()?.mouthOpen).toBeGreaterThan(0);
    expect(controller.snapshot().telemetry.renderedFrames).toBe(1);
    controller.dispose();
  });

  it("bounds and flushes cues received before renderer readiness", async () => {
    const clock = new ManualAnimationClock();
    const controller = new AvatarController(
      new FakeAvatarRenderer(),
      AVATAR_LAB_MANIFEST,
      { clock, maxPreReadyCues: 2 },
    );
    controller.applyCue(cue("state", "listening"));
    controller.applyCue(cue("state", "thinking"));
    controller.applyCue(cue("expression", "happy"));

    expect(controller.snapshot().preReadyQueueSize).toBe(2);
    expect(controller.snapshot().warnings.at(-1)?.code).toBe(
      "avatar.pre_ready_queue_full",
    );
    await controller.load();
    expect(controller.snapshot().preReadyQueueSize).toBe(0);
    controller.dispose();
  });

  it("returns semantic interaction events instead of renderer identifiers", async () => {
    const renderer = new FakeAvatarRenderer();
    const controller = new AvatarController(renderer, AVATAR_LAB_MANIFEST, {
      clock: new ManualAnimationClock(),
    });
    await controller.load();
    controller.resize(600, 600);

    const events = controller.handlePointer(300, 180);

    expect(events[0]?.target).toBe("touched_head");
    expect(events[0]?.kind).toBe("touch");
    controller.dispose();
  });
});
