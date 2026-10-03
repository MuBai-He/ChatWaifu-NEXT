import { afterEach, describe, expect, it, vi } from "vitest";
import * as runtimeEndpoint from "../runtimeEndpoint";

import {
  cancelQQPairing,
  getQQPairing,
  startQQPairing,
  testQQChannelConnection,
} from "./qqClient";

describe("QQ pairing client", () => {
  afterEach(() => {
    runtimeEndpoint.setRemoteRuntimeConnection(null);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each(["start", "poll", "cancel", "health"] as const)(
    "pins %s to the originating Runtime during slow endpoint resolution",
    async (kind) => {
      const first = {
        baseUrl: "https://runtime-a.example",
        token: "private-a",
      };
      runtimeEndpoint.setRemoteRuntimeConnection(first);
      const expectedContext = await runtimeEndpoint.readRuntimeRequestContext();
      let finish!: (value: runtimeEndpoint.RuntimeConnection) => void;
      vi.spyOn(runtimeEndpoint, "resolveRuntimeConnection").mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const options = { expectedContext };
      const request =
        kind === "start"
          ? startQQPairing(
              "ws://127.0.0.1:3001",
              "test-access-token",
              "default",
              options,
            )
          : kind === "poll"
            ? getQQPairing(pending().pairing_id, 20, undefined, options)
            : kind === "cancel"
              ? cancelQQPairing(pending().pairing_id, options)
              : testQQChannelConnection(
                  connection().configuration.connection_id,
                  undefined,
                  options,
                );
      const second = {
        baseUrl: "https://runtime-b.example",
        token: "private-b",
      };
      runtimeEndpoint.setRemoteRuntimeConnection(second);
      finish(second);
      await expect(request).rejects.toThrow("上下文已变化");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("uses the origin credential and discards a late successful pairing response", async () => {
    const first = { baseUrl: "https://runtime-a.example", token: "private-a" };
    runtimeEndpoint.setRemoteRuntimeConnection(first);
    const expectedContext = await runtimeEndpoint.readRuntimeRequestContext();
    let finish!: (value: Response) => void;
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = getQQPairing(pending().pairing_id, 0, undefined, {
      expectedContext,
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `https://runtime-a.example/v1/channel-pairing-sessions/${pending().pairing_id}?wait_seconds=0`,
    );
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization"),
    ).toBe("Bearer private-a");
    runtimeEndpoint.setRemoteRuntimeConnection({
      baseUrl: "https://runtime-b.example",
      token: "private-b",
    });
    finish(jsonResponse(pending()));
    await expect(request).rejects.toThrow("上下文已变化");
  });

  it("sends an authenticated Runtime pairing request without manual owner identifiers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(pending()));
    vi.stubGlobal("fetch", fetchMock);

    expect(
      (
        await startQQPairing(
          "ws://127.0.0.1:3001",
          "test-token-123456789",
          "default",
        )
      ).status,
    ).toBe("pending");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/channel-pairing-sessions$/u);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      schema_version: "1.0",
      provider_id: "qq_napcat",
      endpoint: "ws://127.0.0.1:3001",
      access_token: "test-token-123456789",
      character_id: "default",
    });
  });

  it.each([
    { ...pending(), status: "confirmed", pairing_code: null },
    { ...pending(), status: "pending", pairing_code: null },
    { ...pending(), provider_id: "weixin_ilink" },
    { ...pending(), pairing_id: "arbitrary-id" },
    { ...pending(), expires_at: "not-a-date" },
  ])("rejects an inconsistent pairing snapshot", async (snapshot) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(snapshot)));
    await expect(getQQPairing(pending().pairing_id)).rejects.toThrow(
      "Runtime 返回了无效响应",
    );
  });

  it("bounds long polling to 25 seconds and forwards cancellation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(pending()));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await getQQPairing(pending().pairing_id, 200, controller.signal);
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/wait_seconds=25$/u);
    controller.abort();
    await expect(
      getQQPairing(pending().pairing_id, 20, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("uses DELETE to cancel and parses connection health from the test route", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ removed: true, pairing_id: pending().pairing_id }),
      )
      .mockResolvedValueOnce(jsonResponse(connection()));
    vi.stubGlobal("fetch", fetchMock);
    await cancelQQPairing(pending().pairing_id);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "DELETE" });
    const result = await testQQChannelConnection(
      connection().configuration.connection_id,
    );
    expect(result.status).toBe("ready");
    expect(String(fetchMock.mock.calls[1]?.[0])).toMatch(
      /\/channel-connections\/00000000-0000-4000-8000-000000000202\/test$/u,
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
  });
});

function pending() {
  return {
    schema_version: "1.0",
    pairing_id: "00000000-0000-4000-8000-000000000201",
    provider_id: "qq_napcat",
    status: "pending",
    pairing_code: "PAIR1234",
    account_label: "角色 QQ",
    expires_at: "2026-10-03T12:00:00+08:00",
    connection: null,
    error: null,
  };
}

function connection() {
  return {
    schema_version: "1.0",
    configuration: {
      connection_id: "00000000-0000-4000-8000-000000000202",
      provider_id: "qq_napcat",
      name: "角色 QQ",
      character_id: "default",
      principal_scope: "local",
      enabled: true,
    },
    revision: 1,
    status: "ready",
    last_seen_at: null,
    created_at: "2026-10-03T11:00:00+08:00",
    updated_at: "2026-10-03T11:00:00+08:00",
  };
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
