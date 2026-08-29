import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { requestRuntime } from "./http";
import { resolveRuntimeUrl } from "../runtimeEndpoint";

vi.mock("../runtimeEndpoint", () => ({
  resolveRuntimeUrl: vi.fn().mockResolvedValue("http://runtime.test"),
}));

describe("runtime HTTP client", () => {
  beforeEach(() => {
    vi.mocked(resolveRuntimeUrl).mockReset();
    vi.mocked(resolveRuntimeUrl).mockResolvedValue("http://runtime.test");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("aborts a hung request after its bounded timeout", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () =>
              reject(
                init.signal?.reason instanceof Error
                  ? init.signal.reason
                  : new Error("request aborted"),
              ),
            { once: true },
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = requestRuntime(
      "/v1/hung",
      { parse: () => ({ ok: true }) },
      { timeoutMs: 50 },
    );
    const rejection = expect(request).rejects.toThrow(
      "Runtime 请求超时：/v1/hung",
    );
    await vi.advanceTimersByTimeAsync(50);

    await rejection;
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("starts the request timeout only after a slow desktop endpoint resolves", async () => {
    vi.useFakeTimers();
    vi.mocked(resolveRuntimeUrl).mockImplementation(
      () =>
        new Promise<string>((resolve) => {
          window.setTimeout(() => resolve("http://runtime.test"), 100);
        }),
    );
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = requestRuntime(
      "/v1/playback/ack",
      { parse: (payload) => payload as { ok: boolean } },
      { timeoutMs: 25 },
    );
    await vi.advanceTimersByTimeAsync(99);
    expect(fetchMock).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    await expect(request).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("lets the caller cancel while waiting for the desktop endpoint", async () => {
    vi.mocked(resolveRuntimeUrl).mockImplementation(
      () => new Promise<string>(() => undefined),
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const reason = new DOMException("navigation cancelled", "AbortError");

    const request = requestRuntime(
      "/v1/runtime/health",
      { parse: () => ({ ok: true }) },
      { signal: controller.signal, timeoutMs: 25 },
    );
    controller.abort(reason);

    await expect(request).rejects.toBe(reason);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
