import { describe, expect, it } from "vitest";

import { LIVE2D_LAB_MANIFEST } from "../src/default-manifest";
import { AvatarRendererError } from "../src/renderer";
import { Live2DModelLoader } from "../src/live2d/live2d-model-loader";

describe("Live2DModelLoader", () => {
  it("reports an actionable error when proprietary Cubism Core is absent", async () => {
    const loader = new Live2DModelLoader();
    const canvas = {} as HTMLCanvasElement;

    await expect(
      loader.load(canvas, LIVE2D_LAB_MANIFEST),
    ).rejects.toMatchObject<Partial<AvatarRendererError>>({
      code: "avatar.live2d_core_missing",
      action: expect.stringContaining("vendor/live2d/README.md"),
    });
  });
});
