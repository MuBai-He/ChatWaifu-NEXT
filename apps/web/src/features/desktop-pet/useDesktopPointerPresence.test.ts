import { describe, expect, it } from "vitest";

import { isPointInsideWindow } from "./useDesktopPointerPresence";

describe("desktop pointer presence", () => {
  const origin = { x: -430, y: 120 };
  const size = { width: 430, height: 650 };

  it("tracks points inside a desktop window on negative-coordinate displays", () => {
    expect(isPointInsideWindow({ x: -430, y: 120 }, origin, size)).toBe(true);
    expect(isPointInsideWindow({ x: -1, y: 769 }, origin, size)).toBe(true);
  });

  it("excludes the right and bottom edges and points outside the window", () => {
    expect(isPointInsideWindow({ x: 0, y: 300 }, origin, size)).toBe(false);
    expect(isPointInsideWindow({ x: -200, y: 770 }, origin, size)).toBe(false);
    expect(isPointInsideWindow({ x: -431, y: 300 }, origin, size)).toBe(false);
  });
});
