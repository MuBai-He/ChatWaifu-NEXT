import { describe, expect, it } from "vitest";

import { AVATAR_LAB_MANIFEST } from "../src/default-manifest";
import { FakeAvatarRenderer } from "../src/fake-avatar-renderer";

describe("FakeAvatarRenderer", () => {
  it("releases every resource through fifty load/unload cycles", async () => {
    const renderer = new FakeAvatarRenderer();
    for (let cycle = 0; cycle < 50; cycle += 1) {
      await renderer.load(AVATAR_LAB_MANIFEST);
      expect(renderer.diagnostics().resourceCount).toBe(1);
      await renderer.unload();
      expect(renderer.diagnostics().resourceCount).toBe(0);
    }
  });

  it("maps renderer coordinates to semantic hit targets", async () => {
    const renderer = new FakeAvatarRenderer();
    await renderer.load(AVATAR_LAB_MANIFEST);
    renderer.resize(600, 600, 1);

    expect(renderer.hitTest(300, 180)[0]?.semanticTarget).toBe("touched_head");
    expect(renderer.hitTest(300, 450)[0]?.semanticTarget).toBe("touched_body");
    expect(renderer.hitTest(10, 10)).toEqual([]);
  });
});
