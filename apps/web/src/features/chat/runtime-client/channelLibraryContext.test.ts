import { afterEach, describe, expect, it, vi } from "vitest";
import * as endpoint from "../runtimeEndpoint";
import {
  deleteLearnedSticker,
  fetchStickerImageUrl,
  getStickerLibrary,
  getStickerUsage,
  updateStickerLibrarySettings,
} from "./stickerLibraryClient";
import {
  deleteSavedPhoto,
  fetchPhotoImageUrl,
  getPhotoMemory,
  updatePhotoMemorySettings,
} from "./photoMemoryClient";

describe("channel library Runtime isolation", () => {
  afterEach(() => {
    endpoint.setRemoteRuntimeConnection(null);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
  const calls = {
    "read stickers": () => getStickerLibrary(),
    "save sticker learning": () =>
      updateStickerLibrarySettings({
        learning_enabled: true,
        expected_revision: 0,
      }),
    "delete sticker": () => deleteLearnedSticker("learned_" + "0".repeat(32)),
    "sticker image": () => fetchStickerImageUrl("learned_" + "0".repeat(32)),
    "sticker usage": () => getStickerUsage(),
    "read photos": () => getPhotoMemory(),
    "save photo retention": () =>
      updatePhotoMemorySettings({
        retention_enabled: true,
        expected_revision: 0,
      }),
    "delete photo": () =>
      deleteSavedPhoto("00000000-0000-4000-8000-000000000001"),
    "photo image": () =>
      fetchPhotoImageUrl("00000000-0000-4000-8000-000000000001"),
  };
  it.each(Object.entries(calls))(
    "never retargets %s to a new Runtime while resolving",
    async (_name, call) => {
      endpoint.setRemoteRuntimeConnection({
        baseUrl: "https://a.example",
        token: "a",
      });
      let finish!: (value: endpoint.RuntimeConnection) => void;
      vi.spyOn(endpoint, "resolveRuntimeConnection").mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const request = call();
      const next = { baseUrl: "https://b.example", token: "b" };
      endpoint.setRemoteRuntimeConnection(next);
      finish(next);
      await expect(request).rejects.toThrow("上下文已变化");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
  it("does not display an old authenticated image response after Runtime changes", async () => {
    endpoint.setRemoteRuntimeConnection({
      baseUrl: "https://a.example",
      token: "a",
    });
    let finish!: (value: Response) => void;
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = fetchStickerImageUrl("learned_" + "0".repeat(32));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    endpoint.setRemoteRuntimeConnection({
      baseUrl: "https://b.example",
      token: "b",
    });
    finish(
      new Response("old bytes", { headers: { "Content-Type": "image/png" } }),
    );
    await expect(request).rejects.toThrow("上下文已变化");
  });
});
