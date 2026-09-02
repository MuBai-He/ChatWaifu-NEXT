import { beforeEach, describe, expect, it, vi } from "vitest";

const open = vi.fn();
const invoke = vi.fn();

vi.mock("@tauri-apps/plugin-dialog", () => ({ open }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import {
  installWorkerPackArchive,
  selectWorkerPackArchive,
} from "./desktopWorkerPacks";

describe("desktop Worker Pack installer", () => {
  beforeEach(() => {
    open.mockReset();
    invoke.mockReset();
  });

  it("selects one cwpack with the native desktop dialog", async () => {
    open.mockResolvedValue("C:\\Downloads\\voice.cwpack");

    await expect(selectWorkerPackArchive()).resolves.toBe(
      "C:\\Downloads\\voice.cwpack",
    );
    expect(open).toHaveBeenCalledWith(
      expect.objectContaining({
        multiple: false,
        directory: false,
        filters: [{ name: "ChatWaifu Worker Pack", extensions: ["cwpack"] }],
      }),
    );
  });

  it("does not invoke installation when the picker is cancelled", async () => {
    open.mockResolvedValue(null);

    await expect(selectWorkerPackArchive()).resolves.toBeNull();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("passes the selected archive path to the native installer command", async () => {
    const result = {
      action: "installed_and_activated",
      pack_id: "chatwaifu-faster-whisper-base-cpu-int8",
      version: "0.1.0",
      kind: "stt",
      path: "C:\\packs\\whisper\\0.1.0",
      config_path: "C:\\config\\worker-packs.json",
      restart_required: true,
    };
    invoke.mockResolvedValue(result);

    await expect(
      installWorkerPackArchive("C:\\Downloads\\whisper.cwpack"),
    ).resolves.toEqual(result);
    expect(invoke).toHaveBeenCalledWith("install_worker_pack", {
      archivePath: "C:\\Downloads\\whisper.cwpack",
    });
  });
});
