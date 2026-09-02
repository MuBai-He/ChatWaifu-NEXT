import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelChannelAuthorization,
  deleteChannelConnection,
  getChannelAuthorization,
  startChannelAuthorization,
} from "./channelsClient";

describe("external channels client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts QR authorization with only provider and local character context", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(pendingSnapshot()));
    vi.stubGlobal("fetch", fetchMock);

    const snapshot = await startChannelAuthorization("weixin_ilink", "default");

    expect(snapshot.status).toBe("pending");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/channel-auth-sessions$/u);
    expect(init.method).toBe("POST");
    if (typeof init.body !== "string") throw new Error("expected JSON body");
    expect(JSON.parse(init.body)).toEqual({
      provider_id: "weixin_ilink",
      character_id: "default",
    });
  });

  it("uses a bounded long-poll query and rejects an invalid confirmed snapshot", async () => {
    const invalid = {
      ...pendingSnapshot(),
      status: "confirmed",
      qr_code_content: null,
      connection: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(invalid));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      getChannelAuthorization("00000000-0000-4000-8000-000000000201", 120),
    ).rejects.toThrow("Runtime 返回了无效响应");
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/wait_seconds=30$/u);
  });

  it("uses DELETE to cancel authorization and remove a binding", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(jsonResponse({ ok: true })));
    vi.stubGlobal("fetch", fetchMock);

    await cancelChannelAuthorization("00000000-0000-4000-8000-000000000201");
    await deleteChannelConnection("00000000-0000-4000-8000-000000000202");

    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "DELETE" });
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/v1\/channel-auth-sessions\/00000000-0000-4000-8000-000000000201$/u,
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "DELETE" });
    expect(String(fetchMock.mock.calls[1]?.[0])).toMatch(
      /\/v1\/channel-connections\/00000000-0000-4000-8000-000000000202$/u,
    );
  });
});

function pendingSnapshot() {
  return {
    schema_version: "1.0",
    auth_session_id: "00000000-0000-4000-8000-000000000201",
    provider_id: "weixin_ilink",
    method: "qr_code",
    status: "pending",
    qr_code_content: "weixin://pair/session-1",
    verification_required: false,
    connection: null,
    error: null,
    status_message: "等待扫码",
    poll_after_ms: 1_000,
    expires_at: "2026-08-31T10:30:00+08:00",
    created_at: "2026-08-31T10:00:00+08:00",
    updated_at: "2026-08-31T10:00:00+08:00",
  };
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
