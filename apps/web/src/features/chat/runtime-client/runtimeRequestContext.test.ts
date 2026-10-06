import { afterEach, describe, expect, it, vi } from "vitest";
import * as endpoint from "../runtimeEndpoint";
import { requestRuntime } from "./http";
const first = {
  baseUrl: "https://first.example",
  token: "private-first",
  restartCount: 1,
};
afterEach(() => {
  endpoint.setRemoteRuntimeConnection(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe("pinned Runtime HTTP context", () => {
  it("changes synchronously for server, credential and restart identity but not ordinary reads", async () => {
    endpoint.setRemoteRuntimeConnection(first);
    const listener = vi.fn();
    const unsubscribe = endpoint.subscribeRuntimeContext(listener);
    const initial = endpoint.getRuntimeContextRevision();
    const context = await endpoint.readRuntimeRequestContext();
    await endpoint.resolveRuntimeConnection();
    endpoint.setRemoteRuntimeConnection({ ...first });
    expect(endpoint.getRuntimeContextRevision()).toBe(initial);
    endpoint.setRemoteRuntimeConnection({ ...first, token: "rotated-private" });
    expect(listener).toHaveBeenCalledOnce();
    expect(() => endpoint.assertRuntimeRequestContext(context)).toThrow(
      "上下文已变化",
    );
    endpoint.setRemoteRuntimeConnection({ ...first, restartCount: 2 });
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
  });
  it("sends zero mutation fetches when the context changes during endpoint resolution", async () => {
    endpoint.setRemoteRuntimeConnection(first);
    const context = await endpoint.readRuntimeRequestContext();
    let finish!: (value: endpoint.RuntimeConnection) => void;
    vi.spyOn(endpoint, "resolveRuntimeConnection").mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = requestRuntime(
      "/v1/test",
      { parse: (value) => value },
      { method: "POST", expectedContext: context },
    );
    endpoint.setRemoteRuntimeConnection({
      ...first,
      baseUrl: "https://second.example",
    });
    finish(first);
    await expect(request).rejects.toThrow("上下文已变化");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("discards a successful old response after both online servers switch", async () => {
    endpoint.setRemoteRuntimeConnection(first);
    const context = await endpoint.readRuntimeRequestContext();
    let finish!: (value: Response) => void;
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const parser = vi.fn((value: unknown) => value);
    const request = requestRuntime(
      "/v1/test",
      { parse: parser },
      { expectedContext: context },
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    endpoint.setRemoteRuntimeConnection({
      ...first,
      baseUrl: "https://second.example",
    });
    finish(
      new Response("{}", { headers: { "Content-Type": "application/json" } }),
    );
    await expect(request).rejects.toThrow("上下文已变化");
    expect(parser).not.toHaveBeenCalled();
  });
});
