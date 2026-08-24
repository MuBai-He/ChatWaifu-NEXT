import { describe, expect, it, vi } from "vitest";

import { AvatarCapabilityRegistry } from "../src/capability-registry";
import { CueScheduler } from "../src/cue-scheduler";
import { AVATAR_LAB_MANIFEST } from "../src/default-manifest";
import { cue } from "./fixtures";

function scheduler() {
  return new CueScheduler(
    new AvatarCapabilityRegistry(AVATAR_LAB_MANIFEST.capabilities),
  );
}

describe("CueScheduler", () => {
  it("mixes cues across layers and replaces the same layer by priority", () => {
    const subject = scheduler();
    subject.schedule(cue("state", "listening", { priority: 40 }), 0);
    subject.schedule(cue("expression", "happy"), 1);
    subject.schedule(cue("state", "thinking", { priority: 50 }), 2);

    const snapshot = subject.snapshot();
    expect(snapshot.active.attention?.cue.name).toBe("thinking");
    expect(snapshot.active.emotion?.cue.name).toBe("happy");
  });

  it("queues behind a non-interruptible cue and promotes it after expiry", () => {
    const subject = scheduler();
    subject.schedule(
      cue("motion", "stare", {
        duration_ms: 100,
        interruptible: false,
        priority: 90,
      }),
      0,
    );
    subject.schedule(cue("motion", "headpat", { priority: 100 }), 10);

    expect(subject.snapshot().queued).toHaveLength(1);
    const completed = subject.tick(101);
    expect(completed.active.gesture?.cue.name).toBe("headpat");
    expect(completed.queued).toHaveLength(0);
  });

  it("honors after_current_motion and emits motion lifecycle callbacks", () => {
    const lifecycle = vi.fn();
    const subject = new CueScheduler(
      new AvatarCapabilityRegistry(AVATAR_LAB_MANIFEST.capabilities),
      { onMotionLifecycle: lifecycle },
    );
    const stare = cue("motion", "stare", { duration_ms: 500 });
    subject.schedule(stare, 0);
    subject.schedule(
      cue("motion", "headpat", { start_anchor: "after_current_motion" }),
      5,
    );

    expect(subject.snapshot().queued).toHaveLength(1);
    subject.notifyMotionEnded(stare.cue_id, 100);
    expect(subject.snapshot().active.gesture?.cue.name).toBe("headpat");
    expect(lifecycle).toHaveBeenCalledWith(
      expect.objectContaining({ type: "motion-started" }),
    );
    expect(lifecycle).toHaveBeenCalledWith(
      expect.objectContaining({ type: "motion-ended" }),
    );
  });

  it("invalidates active, queued, and late cues from a generation", () => {
    const generationId = "00000000-0000-4000-8000-000000000900";
    const subject = scheduler();
    subject.schedule(
      cue("state", "speaking", { generation_id: generationId }),
      0,
    );
    subject.invalidateGeneration(generationId, 10);
    subject.schedule(
      cue("expression", "happy", { generation_id: generationId }),
      20,
    );

    const snapshot = subject.snapshot();
    expect(snapshot.active.attention).toBeUndefined();
    expect(snapshot.active.emotion).toBeUndefined();
    expect(snapshot.warnings.at(-1)?.code).toBe("avatar.stale_generation_cue");
  });

  it("falls back to idle when a motion is missing", () => {
    const subject = scheduler();
    const snapshot = subject.schedule(cue("motion", "backflip"), 0);

    expect(snapshot.active.attention?.cue.name).toBe("idle");
    expect(snapshot.active.gesture).toBeUndefined();
    expect(snapshot.warnings.at(-1)?.code).toBe("avatar.capability_missing");
  });

  it("keeps the queue bounded", () => {
    const subject = new CueScheduler(
      new AvatarCapabilityRegistry(AVATAR_LAB_MANIFEST.capabilities),
      { maxQueueSize: 2 },
    );
    subject.schedule(
      cue("motion", "stare", { interruptible: false, duration_ms: 1_000 }),
      0,
    );
    subject.schedule(cue("motion", "headpat"), 1);
    subject.schedule(cue("motion", "stare"), 2);
    subject.schedule(cue("motion", "headpat"), 3);

    expect(subject.snapshot().queued).toHaveLength(2);
    expect(subject.snapshot().warnings.at(-1)?.code).toBe(
      "avatar.cue_queue_full",
    );
  });
});
