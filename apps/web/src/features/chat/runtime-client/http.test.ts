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

  it("bounds desktop endpoint resolution with the same request deadline", async () => {
    vi.useFakeTimers();
    vi.mocked(resolveRuntimeUrl).mockImplementation(
      () => new Promise<string>(() => undefined),
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = requestRuntime(
      "/v1/playback/ack",
      { parse: () => ({ ok: true }) },
      { timeoutMs: 25 },
    );
    const rejection = expect(request).rejects.toThrow(
      "Runtime 请求超时：/v1/playback/ack",
    );
    await vi.advanceTimersByTimeAsync(25);

    await rejection;
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
