import { describe, expect, it } from "vitest";

import { connectionDetail, connectionLabel } from "./desktopRuntimeStatus";

describe("desktop Runtime status copy", () => {
  it("explains that local models are loading during a cold start", () => {
    expect(connectionLabel("connecting")).toBe("正在启动");
    expect(connectionDetail("connecting")).toContain("首次启动可能需要几分钟");
  });

  it("shows the Runtime version only after connection", () => {
    expect(connectionDetail("connected", "0.1.0")).toBe("Runtime 0.1.0");
    expect(connectionLabel("offline")).toBe("Runtime 离线");
  });
});
