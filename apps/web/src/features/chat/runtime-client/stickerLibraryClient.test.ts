import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteLearnedSticker,
  fetchStickerImageUrl,
  getStickerLibrary,
  updateStickerLibrarySettings,
  type StickerLibrarySnapshot,
} from "./stickerLibraryClient";

describe("stickerLibraryClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const sampleSnapshot: StickerLibrarySnapshot = {
    schema_version: "1.0",
    settings: {
      schema_version: "1.0",
      learning_enabled: true,
      revision: 3,
    },
    items: [
      {
        schema_version: "1.0",
        sticker_id: "learned_0123456789abcdef0123456789abcdef",
        sha256:
          "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        mime_type: "image/png",
        label: "开心小猫",
        description: "一只开心笑着的小猫",
        expression: "happy",
        byte_size: 1024,
        learned_at: "2026-09-01T12:00:00Z",
        source_connection_id: "00000000-0000-4000-8000-000000000101",
      },
    ],
    total_bytes: 1024,
    capacity: 100,
  };

  it("gets sticker library snapshot with character_id parameter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(sampleSnapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getStickerLibrary("default");
    expect(result.items).toHaveLength(1);
    expect(result.settings.learning_enabled).toBe(true);

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/sticker-library\?character_id=default$/u);
  });

  it("updates sticker library settings via PUT", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "1.0",
          learning_enabled: true,
          revision: 4,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const updated = await updateStickerLibrarySettings(
      {
        schema_version: "1.0",
        learning_enabled: true,
        expected_revision: 3,
      },
      "default",
    );

    expect(updated.revision).toBe(4);
    expect(updated.learning_enabled).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(
      /\/v1\/sticker-library\/settings\?character_id=default$/u,
    );
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({
      schema_version: "1.0",
      learning_enabled: true,
      expected_revision: 3,
    });
  });

  it("deletes a learned sticker via DELETE", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "1.0",
          deleted: true,
          revision: 4,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteLearnedSticker(
      "learned_0123456789abcdef0123456789abcdef",
      "default",
    );
    expect(result.deleted).toBe(true);
    expect(result.revision).toBe(4);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(
      /\/v1\/sticker-library\/learned_0123456789abcdef0123456789abcdef\?character_id=default$/u,
    );
    expect(init.method).toBe("DELETE");
  });

  it("rejects invalid snapshot schema", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          invalid: "data",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getStickerLibrary("default")).rejects.toThrow(
      "Runtime 返回了无效响应",
    );
  });

  it("fetches sticker image binary with Bearer auth and no query token", async () => {
    // Use bytes: jsdom Blob lacks the stream() required by Node 22 Response.
    const imageBytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
    const createObjectURLMock = vi
      .fn()
      .mockReturnValue("blob:http://localhost/fake-blob-id");
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: createObjectURLMock,
      revokeObjectURL: vi.fn(),
    });

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(imageBytes, {
        status: 200,
        headers: { "Content-Type": "image/png" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const objectUrl = await fetchStickerImageUrl(
      "learned_0123456789abcdef0123456789abcdef",
      { characterId: "default" },
    );

    expect(objectUrl).toBe("blob:http://localhost/fake-blob-id");
    expect(createObjectURLMock).toHaveBeenCalledTimes(1);
    const previewBlob = createObjectURLMock.mock.calls[0][0] as Blob;
    expect(previewBlob.type).toBe("image/png");
    expect(new Uint8Array(await previewBlob.arrayBuffer())).toEqual(imageBytes);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(
      /\/v1\/sticker-library\/learned_0123456789abcdef0123456789abcdef\/image\?character_id=default$/u,
    );
    expect(url).not.toContain("token=");
    expect(init.headers).toBeDefined();
    const headers = init.headers as Record<string, string>;
    expect(headers["Cache-Control"]).toBeUndefined();
    expect(init.cache).toBe("no-store");
  });

  it("aborts image fetch when signal is cancelled", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(
      fetchStickerImageUrl("learned_0123456789abcdef0123456789abcdef", {
        signal: controller.signal,
      }),
    ).rejects.toThrow();
  });

  it("aborts image fetch immediately after resolveRuntimeConnection if signal aborted during connection resolve", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    // Simulate signal aborting during resolveRuntimeConnection
    const promise = fetchStickerImageUrl(
      "learned_0123456789abcdef0123456789abcdef",
      { signal: controller.signal },
    );
    controller.abort(new DOMException("Cancelled before fetch", "AbortError"));

    await expect(promise).rejects.toThrow("Cancelled before fetch");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not leak raw sticker UUID in timeout error message", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn(
        (_url: string | URL | Request, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => {
              reject(new DOMException("Aborted", "AbortError"));
            });
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const promise = fetchStickerImageUrl(
        "learned_0123456789abcdef0123456789abcdef",
        { timeoutMs: 50 },
      );

      const rejection = expect(promise).rejects.toThrow("表情图片请求超时");
      await vi.advanceTimersByTimeAsync(60);
      await rejection;

      // Verify the raw sticker UUID is NOT part of the timeout message
      try {
        await promise;
      } catch (err: unknown) {
        expect((err as Error).message).not.toContain(
          "learned_0123456789abcdef0123456789abcdef",
        );
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects image response when Content-Type is not image/png", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("not png", {
        status: 200,
        headers: { "Content-Type": "image/jpeg" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchStickerImageUrl("learned_0123456789abcdef0123456789abcdef"),
    ).rejects.toThrow("表情图片类型错误 (image/jpeg)，仅支持 PNG");
  });

  it("rejects image response when Content-Length exceeds 5MiB", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("oversized", {
        status: 200,
        headers: {
          "Content-Type": "image/png",
          "Content-Length": String(6 * 1024 * 1024),
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchStickerImageUrl("learned_0123456789abcdef0123456789abcdef"),
    ).rejects.toThrow("表情图片体积超出上限（最大支持 5MB）");
  });

  it("rejects blob exceeding 5MiB", async () => {
    const oversizedBlob = {
      type: "image/png",
      size: 5 * 1024 * 1024 + 1,
    } as unknown as Blob;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "image/png" }),
      blob: () => Promise.resolve(oversizedBlob),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchStickerImageUrl("learned_0123456789abcdef0123456789abcdef"),
    ).rejects.toThrow("表情图片体积超出上限（最大支持 5MB）");
  });

  it("checks post-body abort when caller signal aborts while reading body", async () => {
    const controller = new AbortController();
    const response = new Response("png bytes", {
      status: 200,
      headers: { "Content-Type": "image/png" },
    });
    // Hook blob to abort right after blob resolution
    const originalBlob = response.blob.bind(response);
    response.blob = async () => {
      const result = await originalBlob();
      controller.abort(new DOMException("Cancelled post-body", "AbortError"));
      return result;
    };

    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchStickerImageUrl("learned_0123456789abcdef0123456789abcdef", {
        signal: controller.signal,
      }),
    ).rejects.toThrow("Cancelled post-body");
  });
});
