import { describe, expect, it } from "vitest";

import { resolveWebSurface } from "./webSurface";

describe("Web product surfaces", () => {
  it("owns the browser application, Avatar Lab and shared channel settings", () => {
    expect(resolveWebSurface("/")).toBe("application");
    expect(resolveWebSurface("/avatar-lab")).toBe("avatar-lab");
    expect(resolveWebSurface("/settings/channels")).toBe("channels-settings");
  });

  it.each(["/desktop-pet", "/desktop-settings", "/control-center"])(
    "does not expose the native %s route",
    (pathname) => {
      expect(resolveWebSurface(pathname)).toBe("application");
    },
  );
});
