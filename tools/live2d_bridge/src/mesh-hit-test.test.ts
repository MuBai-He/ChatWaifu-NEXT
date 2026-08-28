import { describe, expect, it } from "vitest";

import {
  isPointInVisibleDrawableMeshes,
  type DrawableMeshSource,
} from "./mesh-hit-test";

type Drawable = {
  visible: boolean;
  opacity: number;
  vertices: number[];
  indices: number[];
};

describe("Live2D visible mesh hit testing", () => {
  it("hits visible mesh triangles beyond authored semantic hit areas", () => {
    const model = drawableModel([
      {
        visible: true,
        opacity: 1,
        vertices: [-1, -1, 1, -1, 0, 1],
        indices: [0, 1, 2],
      },
    ]);

    expect(isPointInVisibleDrawableMeshes(model, 0, 0)).toBe(true);
    expect(isPointInVisibleDrawableMeshes(model, 1, 1)).toBe(false);
  });

  it("ignores hidden and transparent drawables", () => {
    const model = drawableModel([
      {
        visible: false,
        opacity: 1,
        vertices: [-1, -1, 1, -1, 0, 1],
        indices: [0, 1, 2],
      },
      {
        visible: true,
        opacity: 0,
        vertices: [-1, -1, 1, -1, 0, 1],
        indices: [0, 1, 2],
      },
    ]);

    expect(isPointInVisibleDrawableMeshes(model, 0, 0)).toBe(false);
  });

  it("ignores degenerate mesh triangles", () => {
    const model = drawableModel([
      {
        visible: true,
        opacity: 1,
        vertices: [-1, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
      },
    ]);

    expect(isPointInVisibleDrawableMeshes(model, 0, 0)).toBe(false);
  });
});

function drawableModel(drawables: Drawable[]): DrawableMeshSource {
  return {
    getDrawableCount: () => drawables.length,
    getDrawableDynamicFlagIsVisible: (index) =>
      drawables[index]?.visible ?? false,
    getDrawableOpacity: (index) => drawables[index]?.opacity ?? 0,
    getDrawableVertexCount: (index) =>
      (drawables[index]?.vertices.length ?? 0) / 2,
    getDrawableVertices: (index) =>
      new Float32Array(drawables[index]?.vertices ?? []),
    getDrawableVertexIndexCount: (index) =>
      drawables[index]?.indices.length ?? 0,
    getDrawableVertexIndices: (index) =>
      new Uint16Array(drawables[index]?.indices ?? []),
  };
}
