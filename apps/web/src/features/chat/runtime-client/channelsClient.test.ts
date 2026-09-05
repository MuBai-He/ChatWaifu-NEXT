import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelChannelAuthorization,
  deleteChannelConnection,
  getChannelAuthorization,
  startChannelAuthorization,
  updateChannelConnection,
  updateChannelPresentationPolicy,
  type ChannelConnectionSnapshot,
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

  it("updates channel connection via PUT with expected revision and body", async () => {
    const initial = sampleConnection();
    const updatedConfig = {
      ...initial.configuration,
      presentation_policy: {
        ...initial.configuration.presentation_policy!,
        stickers_enabled: true,
      },
    };
    const fetchMock = vi
      .fn()
      .mockImplementation((_url: string, init?: RequestInit) => {
        if (!init || typeof init.body !== "string") {
          throw new Error("expected string body");
        }
        const parsedBody = JSON.parse(
          init.body,
        ) as ChannelConnectionSnapshot["configuration"];
        return Promise.resolve(
          jsonResponse({
            schema_version: "1.0",
            connection: {
              ...initial,
              revision: 2,
              configuration: parsedBody,
            },
          }),
        );
      });
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateChannelConnection(
      initial.configuration.connection_id,
      updatedConfig,
      initial.revision,
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(
      /\/v1\/channel-connections\/00000000-0000-4000-8000-000000000202\?expected_revision=1$/u,
    );
    expect(init.method).toBe("PUT");
    expect(result.revision).toBe(2);
    expect(result.configuration.presentation_policy?.stickers_enabled).toBe(
      true,
    );
  });

  it("updates presentation policy preserving existing policy and connection fields", async () => {
    const initial = sampleConnection();
    const fetchMock = vi
      .fn()
      .mockImplementation((_url: string, init?: RequestInit) => {
        if (!init || typeof init.body !== "string") {
          throw new Error("expected string body");
        }
        const parsedBody = JSON.parse(
          init.body,
        ) as ChannelConnectionSnapshot["configuration"];
        return Promise.resolve(
          jsonResponse({
            schema_version: "1.0",
            connection: {
              ...initial,
              revision: 2,
              configuration: parsedBody,
            },
          }),
        );
      });
    vi.stubGlobal("fetch", fetchMock);

    const nextPolicy = {
      ...initial.configuration.presentation_policy!,
      stickers_enabled: true,
    };
    const result = await updateChannelPresentationPolicy(initial, nextPolicy);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(
      /\/v1\/channel-connections\/00000000-0000-4000-8000-000000000202\?expected_revision=1$/u,
    );
    if (typeof init.body !== "string") throw new Error("expected string body");
    const body = JSON.parse(
      init.body,
    ) as ChannelConnectionSnapshot["configuration"];
    expect(body.name).toBe("我的微信");
    expect(body.character_id).toBe("default");
    expect(body.principal_scope).toBe("local");
    expect(body.presentation_policy?.profile).toBe("instant_message");
    expect(body.presentation_policy?.cadence_enabled).toBe(true);
    expect(body.presentation_policy?.min_delay_ms).toBe(800);
    expect(body.presentation_policy?.max_delay_ms).toBe(3000);
    expect(body.presentation_policy?.stickers_enabled).toBe(true);
    expect(result.configuration.presentation_policy?.stickers_enabled).toBe(
      true,
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

function sampleConnection(stickersEnabled = false): ChannelConnectionSnapshot {
  return {
    configuration: {
      connection_id: "00000000-0000-4000-8000-000000000202",
      provider_id: "weixin_ilink",
      name: "我的微信",
      character_id: "default",
      principal_scope: "local",
      account_key: "owner-key",
      allowed_sender_keys: ["sender-1"],
      enabled: true,
      presentation_policy: {
        profile: "instant_message",
        cadence_enabled: true,
        min_delay_ms: 800,
        max_delay_ms: 3000,
        total_cadence_delay_ceiling_ms: 6000,
        stickers_enabled: stickersEnabled,
      },
    },
    revision: 1,
    status: "ready",
    capabilities: {
      chat_types: ["direct"],
      inbound_message_kinds: ["text"],
      outbound_message_kinds: ["text"],
      authorization_methods: ["qr_code"],
      supports_typing: true,
      supports_partial_replies: false,
      supports_delivery_ack: true,
      supports_cancellation: true,
      supports_proactive_messages: false,
      max_text_chars: 20000,
    },
    last_seen_at: null,
    created_at: "2026-08-31T09:00:00+08:00",
    updated_at: "2026-08-31T10:00:00+08:00",
  };
}
