import { describe, expect, it } from "vitest";

import { LIVE2D_LAB_MANIFEST } from "../src/default-manifest";

describe("default Live2D model identity", () => {
  it("keeps the public model id, interaction id, and attribution aligned", () => {
    expect(LIVE2D_LAB_MANIFEST.avatarId).toBe("ayachi-nene-local");
    expect(LIVE2D_LAB_MANIFEST.capabilities.avatar_id).toBe(
      LIVE2D_LAB_MANIFEST.avatarId,
    );
    expect(LIVE2D_LAB_MANIFEST.attribution).toMatchObject({
      modelAuthor: "涂抹一画",
      sourceUrl: "https://www.bilibili.com/video/BV1MLgYzmEz9",
    });
  });
});
