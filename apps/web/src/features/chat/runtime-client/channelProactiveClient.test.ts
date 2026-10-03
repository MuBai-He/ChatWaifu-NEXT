import { afterEach, describe, expect, it, vi } from "vitest";
import { parseChannelProactivePolicy } from "@chatwaifu/protocol";
import {
  getChannelProactivePolicy,
  updateChannelProactivePolicy,
  previewChannelProactivePolicy,
  getChannelOutboundIntents,
  cancelChannelOutboundIntent,
} from "./channelProactiveClient";
import { RuntimeRequestError } from "./http";

vi.mock("../runtimeEndpoint", () => ({
  resolveRuntimeConnection: () =>
    Promise.resolve({
      baseUrl: "http://127.0.0.1:8771",
      token: "test-runtime-capability",
    }),
}));
const id = "00000000-0000-4000-8000-000000000001";
const now = "2026-10-03T10:00:00+08:00";
afterEach(() => vi.unstubAllGlobals());

describe("operator proactive client", () => {
  it("reads a parsed disabled policy with Runtime authentication", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(response({ connection_id: id }));
    vi.stubGlobal("fetch", fetchMock);
    expect((await getChannelProactivePolicy(id)).policy.enabled).toBe(false);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      `http://127.0.0.1:8771/v1/channel-connections/${id}/proactive-policy`,
    );
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Bearer test-runtime-capability",
    );
  });
  it("creates policy through explicit PUT CAS zero, without a destination field", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        response({ connection_id: id, revision: 1, policy: { enabled: true } }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await updateChannelProactivePolicy(
      id,
      parseChannelProactivePolicy({ enabled: true }),
      0,
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("PUT");
    if (typeof init.body !== "string") throw new Error("expected JSON body");
    expect(JSON.parse(init.body)).toEqual({
      schema_version: "1.0",
      expected_revision: 0,
      policy: parseChannelProactivePolicy({ enabled: true }),
    });
    const expanded = {
      ...parseChannelProactivePolicy({}),
      recipient: "other-owner",
    };
    expect(() => updateChannelProactivePolicy(id, expanded, 0)).toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("qualification uses empty POST, and history uses a bounded encoded cursor", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          connection_id: id,
          policy_revision: 0,
          eligible: false,
          reason: "disabled",
          evaluated_at: now,
        }),
      )
      .mockResolvedValueOnce(response({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await previewChannelProactivePolicy(id);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: "{}",
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      /\/proactive-preview$/u,
    );
    await getChannelOutboundIntents(id, "opaque&cursor/next");
    const url = new URL(String(fetchMock.mock.calls[1]?.[0]));
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.get("cursor")).toBe("opaque&cursor/next");
  });
  it("cancel returns the actual latest receipt rather than an untyped success", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        request_id: id,
        connection_id: id,
        binding_id: id,
        session_id: id,
        turn_id: id,
        generation_id: id,
        status: "settled",
        policy_revision: 1,
        route_revision: 1,
        revision: 3,
        not_before_at: now,
        expires_at: now,
        created_at: now,
        updated_at: now,
        delivery_status: "delivered",
        provider_receipt_present: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await cancelChannelOutboundIntent(id, id, 2);
    expect(result.provider_receipt_present).toBe(true);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ schema_version: "1.0", expected_revision: 2 }),
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(
      new RegExp(`/outbound-intents/${id}/cancel$`),
    );
  });
  it("preserves conflict status and never retries a mutation", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(response({ detail: "revision conflict" }, 409));
    vi.stubGlobal("fetch", fetchMock);
    await expect(cancelChannelOutboundIntent(id, id, 0)).rejects.toMatchObject({
      status: 409,
      name: "RuntimeRequestError",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(RuntimeRequestError).toBeDefined();
  });
  it("rejects malformed history and honors an already-aborted request without fetch", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(response({ items: [{ request_id: id }] }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getChannelOutboundIntents(id)).rejects.toThrow(
      "Runtime 返回了无效响应",
    );
    const controller = new AbortController();
    controller.abort();
    await expect(
      getChannelProactivePolicy(id, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
