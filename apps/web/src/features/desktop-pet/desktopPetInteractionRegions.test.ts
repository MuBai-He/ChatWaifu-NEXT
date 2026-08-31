import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  isPointInsideDesktopPetChrome,
  isPointInsideRect,
} from "./desktopPetInteractionRegions";

describe("desktop pet native interaction regions", () => {
  afterEach(cleanup);

  it("keeps only marked visible chrome interactive", () => {
    const actionRail = document.createElement("nav");
    actionRail.dataset.nativeInteractive = "true";
    actionRail.getBoundingClientRect = () =>
      ({ left: 270, top: 608, right: 418, bottom: 638 }) as DOMRect;
    const unmarkedCanvas = document.createElement("canvas");
    unmarkedCanvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, right: 430, bottom: 650 }) as DOMRect;
    document.body.append(unmarkedCanvas, actionRail);

    expect(isPointInsideDesktopPetChrome(document, { x: 300, y: 620 })).toBe(
      true,
    );
    expect(isPointInsideDesktopPetChrome(document, { x: 100, y: 100 })).toBe(
      false,
    );

    actionRail.remove();
    unmarkedCanvas.remove();
  });

  it("uses half-open bounds so adjacent transparent pixels are not captured", () => {
    const rect = { left: 10, top: 20, right: 40, bottom: 60 };
    expect(isPointInsideRect({ x: 10, y: 20 }, rect)).toBe(true);
    expect(isPointInsideRect({ x: 39.9, y: 59.9 }, rect)).toBe(true);
    expect(isPointInsideRect({ x: 40, y: 30 }, rect)).toBe(false);
    expect(isPointInsideRect({ x: 20, y: 60 }, rect)).toBe(false);
  });
});
