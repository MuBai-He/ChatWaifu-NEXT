import { afterEach, describe, expect, it, vi } from "vitest";

import { verifyWorkerPackIntegrity } from "./workerPacksClient";

describe("Worker Pack integrity client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests an explicit full verification and parses its result", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "1.0",
          valid: true,
          checked_at: "2026-09-02T10:00:00+08:00",
          packs: [
            {
              pack_id: "qwen3-tts-nene-cu126",
              version: "0.1.0",
              kind: "tts",
              backend: "qwen3_tts_torch",
              file_count: 31_223,
              size_bytes: 5_000_000_000,
            },
          ],
          errors: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await verifyWorkerPackIntegrity();

    expect(result.valid).toBe(true);
    expect(result.packs[0]?.file_count).toBe(31_223);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: "{}",
    });
  });

  it("rejects malformed verification responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            schema_version: "1.0",
            valid: "yes",
            checked_at: "now",
            packs: [],
            errors: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(verifyWorkerPackIntegrity()).rejects.toThrow(
      "Runtime 返回了无效响应",
    );
  });
});
