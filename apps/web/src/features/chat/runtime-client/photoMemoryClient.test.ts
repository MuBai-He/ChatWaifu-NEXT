// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteSavedPhoto,
  fetchPhotoImageUrl,
  getPhotoMemory,
  updatePhotoMemorySettings,
  type PhotoMemorySnapshot,
} from "./photoMemoryClient";

describe("photoMemoryClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const sampleSnapshot: PhotoMemorySnapshot = {
    schema_version: "1.0",
    settings: {
      schema_version: "1.0",
      retention_enabled: true,
      revision: 3,
    },
    items: [
      {
        schema_version: "1.0",
        photo_id: "00000000-0000-4000-8000-000000000101",
        sha256:
          "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        mime_type: "image/png",
        byte_size: 1024,
        width: 1024,
        height: 1024,
        title: "Test Photo",
        description: "A test photo",
        confidence: 0.9,
        keywords: ["test"],
        caption: "A caption",
        received_at: "2026-09-01T12:00:00Z",
        saved_at: "2026-09-01T12:00:00Z",
        source_connection_id: "00000000-0000-4000-8000-000000000101",
        source_session_id: "00000000-0000-4000-8000-000000000101",
        source_turn_id: "00000000-0000-4000-8000-000000000101",
        source_generation_id: "00000000-0000-4000-8000-000000000101",
      },
    ],
    total_bytes: 1024,
    capacity: 200,
  };

  it("gets photo memory snapshot with character_id parameter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(sampleSnapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getPhotoMemory("default");
    expect(result.items).toHaveLength(1);
    expect(result.settings.retention_enabled).toBe(true);

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/photo-memory\?character_id=default$/u);
  });

  it("updates photo memory settings via PUT", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "1.0",
          retention_enabled: true,
          revision: 4,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const updated = await updatePhotoMemorySettings(
      {
        schema_version: "1.0",
        retention_enabled: true,
        expected_revision: 3,
      },
      "default",
    );

    expect(updated.revision).toBe(4);
    expect(updated.retention_enabled).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/v1\/photo-memory\/settings\?character_id=default$/u);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({
      schema_version: "1.0",
      retention_enabled: true,
      expected_revision: 3,
    });
  });

  it("deletes a saved photo via DELETE", async () => {
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

    const result = await deleteSavedPhoto(
      "00000000-0000-4000-8000-000000000101",
      "default",
    );
    expect(result.deleted).toBe(true);
    expect(result.revision).toBe(4);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(
      /\/v1\/photo-memory\/00000000-0000-4000-8000-000000000101\?character_id=default$/u,
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

    await expect(getPhotoMemory("default")).rejects.toThrow(
      "Runtime 返回了无效响应",
    );
  });

  it("fetches photo image binary with Bearer auth and no query token", async () => {
    const imageBytes = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
    const createObjectURLMock = vi
      .fn()
      .mockReturnValue("blob:http://localhost/fake-blob-id");
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: createObjectURLMock,
      revokeObjectURL: vi.fn(),
    });

    const mockReader = {
      releaseLock: vi.fn(),
      read: vi
        .fn()
        .mockResolvedValueOnce({ done: false, value: imageBytes })
        .mockResolvedValueOnce({ done: true }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "image/png" }),
      body: { getReader: () => mockReader },
    });
    vi.stubGlobal("fetch", fetchMock);

    const objectUrl = await fetchPhotoImageUrl(
      "00000000-0000-4000-8000-000000000101",
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
      /\/v1\/photo-memory\/00000000-0000-4000-8000-000000000101\/image\?character_id=default$/u,
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
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101", {
        signal: controller.signal,
      }),
    ).rejects.toThrow();
  });

  it("aborts image fetch immediately after resolveRuntimeConnection if signal aborted during connection resolve", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const promise = fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101", {
      signal: controller.signal,
    });
    controller.abort(new DOMException("Cancelled before fetch", "AbortError"));

    await expect(promise).rejects.toThrow("Cancelled before fetch");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not leak raw photo UUID in timeout error message", async () => {
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

      const promise = fetchPhotoImageUrl(
        "00000000-0000-4000-8000-000000000101",
        { timeoutMs: 50 },
      );

      const rejection = expect(promise).rejects.toThrow("照片请求超时");
      await vi.advanceTimersByTimeAsync(60);
      await rejection;

      try {
        await promise;
      } catch (err: unknown) {
        expect((err as Error).message).not.toContain(
          "00000000-0000-4000-8000-000000000101",
        );
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects image response when Content-Type is invalid", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("not png", {
        status: 200,
        headers: { "Content-Type": "image/gif" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101"),
    ).rejects.toThrow("照片格式错误 (image/gif)，仅支持 PNG/JPEG");
  });

  it("rejects image response when missing MIME", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: {},
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101"),
    ).rejects.toThrow("照片格式错误 (缺失)，仅支持 PNG/JPEG");
  });

  it("rejects empty blob", async () => {
    const mockReader = {
      releaseLock: vi.fn(),
      read: vi.fn().mockResolvedValueOnce({ done: true }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "image/png" }),
      body: { getReader: () => mockReader },
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101"),
    ).rejects.toThrow("照片为空");
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
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101"),
    ).rejects.toThrow("照片体积超出上限（最大支持 5MB）");
  });

  it("rejects blob exceeding 5MiB streaming", async () => {
    const chunk = new Uint8Array(5 * 1024 * 1024 + 1);
    const mockReader = {
      releaseLock: vi.fn(),
      read: vi
        .fn()
        .mockResolvedValueOnce({ done: false, value: chunk })
        .mockResolvedValueOnce({ done: true }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "image/png" }),
      body: { getReader: () => mockReader },
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101"),
    ).rejects.toThrow("照片体积超出上限（最大支持 5MB）");
  });

  it("checks post-body abort when caller signal aborts while reading body", async () => {
    const controller = new AbortController();
    const mockReader = {
      releaseLock: vi.fn(),
      read: vi.fn().mockImplementation(() => {
        controller.abort(new DOMException("Cancelled post-body", "AbortError"));
        return Promise.resolve({ done: true });
      }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "image/png" }),
      body: { getReader: () => mockReader },
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000101", {
        signal: controller.signal,
      }),
    ).rejects.toThrow("Cancelled post-body");
  });
});
