import { describe, expect, it } from "vitest";

import {
  getAvatarCanvasLayoutSize,
  mapClientPointToAvatarCanvas,
} from "./avatarCanvasGeometry";

describe("avatar canvas geometry", () => {
  it("resizes the renderer from layout dimensions rather than CSS transforms", () => {
    const canvas = document.createElement("canvas");
    Object.defineProperties(canvas, {
      clientWidth: { configurable: true, value: 400 },
      clientHeight: { configurable: true, value: 500 },
    });
    canvas.getBoundingClientRect = () =>
      DOMRect.fromRect({ width: 536, height: 670 });

    expect(getAvatarCanvasLayoutSize(canvas)).toEqual({
      width: 400,
      height: 500,
    });
  });

  it("maps a point through the visual CSS transform into canvas layout space", () => {
    const canvas = document.createElement("canvas");
    Object.defineProperties(canvas, {
      clientWidth: { configurable: true, value: 400 },
      clientHeight: { configurable: true, value: 500 },
    });
    canvas.getBoundingClientRect = () =>
      DOMRect.fromRect({ x: 80, y: 20, width: 536, height: 670 });

    const point = mapClientPointToAvatarCanvas(canvas, 348, 355);
    expect(point?.x).toBeCloseTo(200);
    expect(point?.y).toBeCloseTo(250);
  });
});
