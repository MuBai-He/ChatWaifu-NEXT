import { beforeEach, describe, expect, it, vi } from "vitest";
import { bootstrapRuntimeSession } from "./chatSessionBootstrap";
import {
  createSession,
  getCharacters,
  getHealth,
  getSession,
} from "./runtimeClient";
vi.mock("./runtimeClient", () => ({
  createSession: vi.fn(),
  getCharacters: vi.fn(),
  getHealth: vi.fn(),
  getSession: vi.fn(),
}));
describe("cancelled settings bootstrap", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getHealth).mockResolvedValue({} as never);
    vi.mocked(getCharacters).mockResolvedValue([
      { character_id: "nene" },
    ] as never);
  });
  it("does not create a replacement session when the saved-session read is aborted", async () => {
    const controller = new AbortController();
    vi.mocked(getSession).mockImplementation(() => {
      controller.abort();
      return Promise.reject(controller.signal.reason);
    });
    const storage = {
      getItem: vi.fn().mockReturnValue("saved"),
      setItem: vi.fn(),
    };
    await expect(
      bootstrapRuntimeSession(storage, controller.signal),
    ).rejects.toThrow();
    expect(createSession).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });
  it("does not overwrite storage when a superseded create response arrives", async () => {
    const controller = new AbortController();
    vi.mocked(createSession).mockImplementation(() => {
      controller.abort();
      return Promise.resolve({ session_id: "stale", state: "ready" } as never);
    });
    const storage = {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
    };
    await expect(
      bootstrapRuntimeSession(storage, controller.signal),
    ).rejects.toThrow();
    expect(createSession).toHaveBeenCalledOnce();
    expect(storage.setItem).not.toHaveBeenCalled();
  });
});
