import { afterEach, describe, expect, it, vi } from "vitest";
import { parseChannelRuntimePolicy } from "@chatwaifu/protocol";
import * as endpoint from "../runtimeEndpoint";
import {
  getChannelRuntimeSettings,
  updateChannelRuntimeSettings,
} from "./channelSettingsClient";

describe("operator channel policy client", () => {
  afterEach(() => {
    endpoint.setRemoteRuntimeConnection(null);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
  it.each(["read", "save"] as const)(
    "pins %s to its original endpoint and credential",
    async (kind) => {
      endpoint.setRemoteRuntimeConnection({
        baseUrl: "https://a.example",
        token: "a",
      });
      const expectedContext = await endpoint.readRuntimeRequestContext();
      let finish!: (value: endpoint.RuntimeConnection) => void;
      vi.spyOn(endpoint, "resolveRuntimeConnection").mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const options = { expectedContext, signal: new AbortController().signal };
      const request =
        kind === "read"
          ? getChannelRuntimeSettings(options)
          : updateChannelRuntimeSettings(
              parseChannelRuntimePolicy({}),
              0,
              options,
            );
      const second = { baseUrl: "https://b.example", token: "b" };
      endpoint.setRemoteRuntimeConnection(second);
      finish(second);
      await expect(request).rejects.toThrow("上下文已变化");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
  it("validates, saves with CAS and rejects invalid response rather than silently accepting", async () => {
    endpoint.setRemoteRuntimeConnection({
      baseUrl: "https://a.example",
      token: "a",
    });
    const options = {
      expectedContext: await endpoint.readRuntimeRequestContext(),
      signal: new AbortController().signal,
    };
    const policy = parseChannelRuntimePolicy({
      qq_owner_voice_reply_enabled: false,
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          revision: 1,
          policy,
          search_provider: "searxng",
          reader_provider: "crawl4ai",
          stt_provider: "disabled",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    expect(
      (await updateChannelRuntimeSettings(policy, 0, options)).policy,
    ).toEqual(policy);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://a.example/v1/channels/settings");
    expect(init?.method).toBe("PUT");
    expect(typeof init?.body).toBe("string");
    expect(JSON.parse(init?.body as string)).toEqual({
      schema_version: "1.0",
      expected_revision: 0,
      policy,
    });
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer a");
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          revision: 2,
          policy: { qq_owner_public_web_enabled: "false" },
        }),
      ),
    );
    await expect(getChannelRuntimeSettings(options)).rejects.toThrow();
  });
});
