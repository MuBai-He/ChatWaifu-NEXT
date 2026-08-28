import { describe, expect, it } from "vitest";

import { resolveAppSurfaceFromContext } from "./appSurface";

describe("application surface routing", () => {
  it("uses explicit browser routes outside the desktop host", () => {
    expect(resolveAppSurfaceFromContext("/desktop-pet", null)).toBe(
      "desktop-pet",
    );
    expect(resolveAppSurfaceFromContext("/desktop-settings", null)).toBe(
      "desktop-settings",
    );
    expect(resolveAppSurfaceFromContext("/avatar-lab", null)).toBe(
      "avatar-lab",
    );
  });

  it("prioritizes the native window role over a stale or missing path", () => {
    expect(resolveAppSurfaceFromContext("/desktop-pet", "control-center")).toBe(
      "desktop-settings",
    );
    expect(resolveAppSurfaceFromContext("/", "avatar-overlay")).toBe(
      "desktop-pet",
    );
  });

  it("prioritizes the host-injected surface over stale Windows WebView context", () => {
    expect(
      resolveAppSurfaceFromContext(
        "/desktop-pet",
        "avatar-overlay",
        "desktop-settings",
        "desktop-pet",
      ),
    ).toBe("desktop-settings");
  });

  it("uses the development URL marker before window-label fallbacks", () => {
    expect(
      resolveAppSurfaceFromContext(
        "/desktop-pet",
        null,
        null,
        "desktop-settings",
      ),
    ).toBe("desktop-settings");
  });
});
