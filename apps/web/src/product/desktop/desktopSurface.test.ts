import { describe, expect, it } from "vitest";

import { resolveDesktopSurfaceFromContext } from "./desktopSurface";

describe("Desktop product surfaces", () => {
  it("defaults unknown and browser-only paths to the pet", () => {
    expect(resolveDesktopSurfaceFromContext("/", null)).toBe("desktop-pet");
    expect(resolveDesktopSurfaceFromContext("/avatar-lab", null)).toBe(
      "desktop-pet",
    );
  });

  it("resolves the control center from its preview path", () => {
    expect(resolveDesktopSurfaceFromContext("/desktop-settings", null)).toBe(
      "desktop-settings",
    );
    expect(resolveDesktopSurfaceFromContext("/control-center", null)).toBe(
      "desktop-settings",
    );
  });

  it("lets immutable native identity override stale paths and labels", () => {
    expect(
      resolveDesktopSurfaceFromContext(
        "/desktop-pet",
        "avatar-overlay",
        "desktop-settings",
      ),
    ).toBe("desktop-settings");
  });

  it("uses the native window label when no marker is available", () => {
    expect(resolveDesktopSurfaceFromContext("/", "control-center")).toBe(
      "desktop-settings",
    );
    expect(
      resolveDesktopSurfaceFromContext("/desktop-settings", "avatar-overlay"),
    ).toBe("desktop-pet");
  });
});
