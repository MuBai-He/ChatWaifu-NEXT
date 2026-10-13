import { afterEach, expect, it, vi } from "vitest";
import { z } from "zod";
import { KnowledgeClient } from "./knowledgeClient";
import {
  requestRuntime,
  RuntimeRequestError,
} from "../chat/runtime-client/http";

vi.mock("../chat/runtime-client/http", async (original) => ({
  ...(await original<typeof import("../chat/runtime-client/http")>()),
  requestRuntime: vi.fn(),
}));
afterEach(() => vi.clearAllMocks());

it("uses the existing authenticated client and the current owner session", async () => {
  vi.mocked(requestRuntime).mockResolvedValue({ value: 1 });
  const controller = new AbortController();
  const client = new KnowledgeClient("owner-session", false, controller.signal);
  expect(
    await client.request("/status", z.object({ value: z.number() })),
  ).toEqual({ value: 1 });
  expect(requestRuntime).toHaveBeenCalledWith(
    "/v1/knowledge/status?session_id=owner-session",
    expect.anything(),
    expect.objectContaining({ signal: controller.signal, timeoutMs: 150000 }),
  );
});

it("explains an unavailable server without confusing it with an empty graph", async () => {
  vi.mocked(requestRuntime).mockRejectedValue(
    new RuntimeRequestError("missing", 404),
  );
  const client = new KnowledgeClient(
    "owner",
    false,
    new AbortController().signal,
  );
  await expect(client.request("/graph", z.object({}))).rejects.toThrow(
    "当前服务器尚未启用知识图谱",
  );
});

it("keeps cancellation and access-denied failures intact", async () => {
  const error = new DOMException("cancelled", "AbortError");
  vi.mocked(requestRuntime).mockRejectedValue(error);
  const client = new KnowledgeClient(
    "owner",
    false,
    new AbortController().signal,
  );
  await expect(client.request("/graph", z.object({}))).rejects.toBe(error);
});
