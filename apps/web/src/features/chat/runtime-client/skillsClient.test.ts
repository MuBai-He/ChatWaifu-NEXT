import { afterEach, describe, expect, it, vi } from "vitest";

import { resolveRuntimeUrl } from "../runtimeEndpoint";
import { getSkillConfirmations } from "./skillsClient";

vi.mock("../runtimeEndpoint", () => ({
  resolveRuntimeUrl: vi.fn().mockResolvedValue("http://runtime.test"),
}));

describe("Runtime Skills client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("validates the bounded argument preview returned for confirmation", async () => {
    vi.mocked(resolveRuntimeUrl).mockResolvedValue("http://runtime.test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            items: [
              {
                request_id: "00000000-0000-4000-8000-000000000021",
                skill_run_id: "00000000-0000-4000-8000-000000000022",
                skill_id: "web.search",
                capability: "search",
                permissions: ["network.read"],
                side_effect: "read",
                reason: "需要联网读取资料",
                requested_at: "2026-08-29T00:00:00Z",
                expires_at: "2026-08-29T00:05:00Z",
                allowed_decisions: ["deny", "allow_once"],
                argument_preview: {
                  text: '{\n  "api_key": "[REDACTED]"\n}',
                  truncated: false,
                  redacted: true,
                },
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const [confirmation] = await getSkillConfirmations("session-1");

    expect(confirmation.argument_preview).toEqual({
      text: '{\n  "api_key": "[REDACTED]"\n}',
      truncated: false,
      redacted: true,
    });
  });
});
