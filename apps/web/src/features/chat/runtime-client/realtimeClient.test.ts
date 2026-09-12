import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getRealtimeConfiguration,
  isConflictError,
  updateRealtimeConfiguration,
} from "./realtimeClient";
import type { RealtimeConfigurationSnapshot } from "./contracts";

const validSnapshot: RealtimeConfigurationSnapshot = {
  schema_version: "1.0",
  revision: 2,
  connection_mode: "cloud_realtime",
  cloud_backend: "openai",
  model: "gpt-4o-realtime-preview",
  voice: "marin",
  transcription_model: "gpt-4o-mini-transcribe",
  cloud_tools_enabled: true,
  cloud_egress_consent: true,
  api_key_configured: true,
  active_connections: 1,
};

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("realtimeClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches realtime configuration snapshot", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(validSnapshot));
    vi.stubGlobal("fetch", fetchMock);

    const snapshot = await getRealtimeConfiguration();

    expect(snapshot).toEqual(validSnapshot);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/realtime\/configuration$/u);
    expect(init.method ?? "GET").toBe("GET");
  });

  it("recognizes revision conflicts by HTTP status even with localized detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ detail: "配置已经更改，请刷新" }, 409),
        ),
    );
    const error = await getRealtimeConfiguration().catch(
      (error: unknown) => error,
    );
    expect(isConflictError(error)).toBe(true);
  });

  it("updates realtime configuration and parses response", async () => {
    const updatedSnapshot = { ...validSnapshot, revision: 3 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(updatedSnapshot));
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateRealtimeConfiguration({
      schema_version: "1.0",
      expected_revision: 2,
      connection_mode: "cloud_realtime",
      cloud_backend: "openai",
      model: "gpt-4o-realtime-preview",
      voice: "marin",
      transcription_model: "gpt-4o-mini-transcribe",
      cloud_tools_enabled: false,
      cloud_egress_consent: true,
      api_key: "sk-test",
      clear_api_key: false,
    });

    expect(result).toEqual(updatedSnapshot);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/realtime\/configuration$/u);
    expect(init.method).toBe("PUT");
    const sentBody = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(sentBody).toEqual({
      schema_version: "1.0",
      expected_revision: 2,
      connection_mode: "cloud_realtime",
      cloud_backend: "openai",
      model: "gpt-4o-realtime-preview",
      voice: "marin",
      transcription_model: "gpt-4o-mini-transcribe",
      cloud_tools_enabled: false,
      cloud_egress_consent: true,
      api_key: "sk-test",
      clear_api_key: false,
    });
  });

  it("rejects when both api_key and clear_api_key are provided", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      updateRealtimeConfiguration({
        schema_version: "1.0",
        expected_revision: 2,
        connection_mode: "cloud_realtime",
        cloud_backend: "openai",
        model: "gpt-4o-realtime-preview",
        voice: "marin",
        transcription_model: "gpt-4o-mini-transcribe",
        cloud_tools_enabled: false,
        cloud_egress_consent: true,
        api_key: "sk-new-key",
        clear_api_key: true,
      }),
    ).rejects.toThrow("不能同时输入新密钥并勾选清除密钥。");

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
