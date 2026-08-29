import { describe, expect, it } from "vitest";

import { resolveWebSurface } from "./WebProductApp";

describe("Web product surfaces", () => {
  it("owns the browser application and Avatar Lab only", () => {
    expect(resolveWebSurface("/")).toBe("application");
    expect(resolveWebSurface("/avatar-lab")).toBe("avatar-lab");
  });

  it.each(["/desktop-pet", "/desktop-settings", "/control-center"])(
    "does not expose the native %s route",
    (pathname) => {
      expect(resolveWebSurface(pathname)).toBe("application");
    },
  );
});
