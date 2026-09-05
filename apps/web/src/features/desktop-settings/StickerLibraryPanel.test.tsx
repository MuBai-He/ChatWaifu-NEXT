import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as runtimeClient from "../chat/runtimeClient";
import type { StickerLibrarySnapshot } from "../chat/runtimeClient";
import { StickerLibraryPanel } from "./StickerLibraryPanel";

vi.mock("../chat/runtimeClient", () => ({
  deleteLearnedSticker: vi.fn(),
  fetchStickerImageUrl: vi.fn(),
  getStickerLibrary: vi.fn(),
  updateStickerLibrarySettings: vi.fn(),
}));

describe("StickerLibraryPanel", () => {
  const sampleSnapshot: StickerLibrarySnapshot = {
    schema_version: "1.0",
    settings: {
      schema_version: "1.0",
      learning_enabled: false,
      revision: 2,
    },
    items: [
      {
        schema_version: "1.0",
        sticker_id: "learned_11111111111111111111111111111111",
        sha256:
          "2222222222222222222222222222222222222222222222222222222222222222",
        mime_type: "image/png",
        label: "摸鱼小猫",
        description: "趴在桌子上摸鱼的猫咪",
        expression: "happy",
        byte_size: 2048,
        learned_at: "2026-09-01T10:00:00Z",
        source_connection_id: "00000000-0000-4000-8000-000000000101",
      },
    ],
    total_bytes: 2048,
    capacity: 100,
  };

  beforeEach(() => {
    vi.mocked(runtimeClient.getStickerLibrary).mockResolvedValue(
      sampleSnapshot,
    );
    vi.mocked(runtimeClient.fetchStickerImageUrl).mockResolvedValue(
      "blob:http://localhost/sticker-1",
    );
    vi.mocked(runtimeClient.updateStickerLibrarySettings).mockResolvedValue({
      schema_version: "1.0",
      learning_enabled: true,
      revision: 3,
    });
    vi.mocked(runtimeClient.deleteLearnedSticker).mockResolvedValue({
      schema_version: "1.0",
      deleted: true,
      revision: 3,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders sticker library with items, friendly chinese labels, and count/capacity", async () => {
    render(<StickerLibraryPanel characterId="default" runtimeOnline={true} />);

    expect(await screen.findByText("学习我发来的表情")).toBeTruthy();
    expect(
      screen.getByText(
        "开启后自动筛选并保存适合作表情的图片，普通照片不进入表情库。",
      ),
    ).toBeTruthy();
    expect(screen.getByText("照片保存与回忆将在后续提供。")).toBeTruthy();
    expect(
      screen.getByText(
        "已学习的表情会在开启“合适的时候发送表情”时由角色主动发出。",
      ),
    ).toBeTruthy();

    expect(screen.getByText("1 / 100")).toBeTruthy();
    expect(screen.getByText("摸鱼小猫")).toBeTruthy();
    expect(screen.getByText("趴在桌子上摸鱼的猫咪")).toBeTruthy();
    expect(screen.getByText("开心")).toBeTruthy(); // friendly chinese expression
    expect(
      screen.getByRole("button", { name: "删除表情 摸鱼小猫" }),
    ).toBeTruthy();
  });

  it("renders empty state when library has no stickers with conservative copy", async () => {
    vi.mocked(runtimeClient.getStickerLibrary).mockResolvedValue({
      schema_version: "1.0",
      settings: {
        schema_version: "1.0",
        learning_enabled: false,
        revision: 0,
      },
      items: [],
      total_bytes: 0,
      capacity: 100,
    });

    render(<StickerLibraryPanel characterId="default" runtimeOnline={true} />);

    expect(await screen.findByText("暂无已学习的表情")).toBeTruthy();
    expect(
      screen.getByText(
        "开启“学习我发来的表情”后，发送的表情图经自动筛选后才会被收录。",
      ),
    ).toBeTruthy();
    expect(screen.getByText("0 / 100")).toBeTruthy();
  });

  it("renders error alert when library fetch fails", async () => {
    vi.mocked(runtimeClient.getStickerLibrary).mockRejectedValue(
      new Error("网络连接超时"),
    );

    render(<StickerLibraryPanel characterId="default" runtimeOnline={true} />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText("网络连接超时")).toBeTruthy();
  });

  it("toggles learning setting sending expected revision and optimistic disable", async () => {
    render(<StickerLibraryPanel characterId="default" runtimeOnline={true} />);

    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "学习我发来的表情",
    });
    expect(toggle.checked).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() =>
      expect(runtimeClient.updateStickerLibrarySettings).toHaveBeenCalledWith(
        {
          schema_version: "1.0",
          learning_enabled: true,
          expected_revision: 2,
        },
        "default",
      ),
    );
  });

  it("deletes sticker and refreshes list", async () => {
    render(<StickerLibraryPanel characterId="default" runtimeOnline={true} />);

    const deleteBtn = await screen.findByRole("button", {
      name: "删除表情 摸鱼小猫",
    });
    fireEvent.click(deleteBtn);

    await waitFor(() =>
      expect(runtimeClient.deleteLearnedSticker).toHaveBeenCalledWith(
        "learned_11111111111111111111111111111111",
        "default",
      ),
    );
    expect(runtimeClient.getStickerLibrary).toHaveBeenCalledTimes(2);
  });

  it("revokes object URLs on unmount", async () => {
    const revokeMock = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      revokeObjectURL: revokeMock,
    });

    const view = render(
      <StickerLibraryPanel characterId="default" runtimeOnline={true} />,
    );

    await screen.findByRole("img", { name: "摸鱼小猫" });

    view.unmount();
    expect(revokeMock).toHaveBeenCalledWith("blob:http://localhost/sticker-1");
  });

  it("ignores stale load responses", async () => {
    let resolveFirst: (v: StickerLibrarySnapshot) => void = () => {};
    let resolveSecond: (v: StickerLibrarySnapshot) => void = () => {};

    // Initial render call
    vi.mocked(runtimeClient.getStickerLibrary).mockResolvedValueOnce(
      sampleSnapshot,
    );

    const { rerender } = render(
      <StickerLibraryPanel characterId="default" runtimeOnline={true} />,
    );

    await screen.findByText("摸鱼小猫");

    // Now set up racing loads on refresh:
    // Request 1 takes long time
    vi.mocked(runtimeClient.getStickerLibrary).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );

    const refreshBtn = screen.getByRole("button", { name: "刷新表情库" });
    fireEvent.click(refreshBtn);

    // Request 2 (e.g. characterId change or another trigger)
    vi.mocked(runtimeClient.getStickerLibrary).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSecond = resolve;
        }),
    );

    rerender(<StickerLibraryPanel characterId="other" runtimeOnline={true} />);

    // Resolve second request first with empty library
    resolveSecond({
      ...sampleSnapshot,
      items: [],
    });

    await waitFor(() => {
      expect(screen.getByText("暂无已学习的表情")).toBeTruthy();
    });

    // Now resolve first (stale) request with items
    resolveFirst(sampleSnapshot);

    // It must NOT overwrite the UI
    await waitFor(() => {
      expect(screen.queryByText("摸鱼小猫")).toBeNull();
      expect(screen.getByText("暂无已学习的表情")).toBeTruthy();
    });
  });

  it("refreshes sticker library after 409 conflict error on settings update", async () => {
    vi.mocked(runtimeClient.getStickerLibrary).mockResolvedValue(
      sampleSnapshot,
    );
    vi.mocked(runtimeClient.updateStickerLibrarySettings).mockRejectedValueOnce(
      new Error("Runtime request failed (409)"),
    );

    render(<StickerLibraryPanel characterId="default" runtimeOnline={true} />);

    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "学习我发来的表情",
    });

    fireEvent.click(toggle);

    // Expect friendly error preserved in UI
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText("Runtime request failed (409)")).toBeTruthy();

    // Verify library was refreshed after conflict
    await waitFor(() => {
      expect(runtimeClient.getStickerLibrary).toHaveBeenCalledTimes(2);
    });
  });

  it("invalidates in-flight loads so mutation is not overwritten by a later-resolving stale snapshot", async () => {
    let resolveStaleLoad: (v: StickerLibrarySnapshot) => void = () => {};

    // Initial render
    vi.mocked(runtimeClient.getStickerLibrary).mockResolvedValueOnce(
      sampleSnapshot,
    );

    render(<StickerLibraryPanel characterId="default" runtimeOnline={true} />);
    await screen.findByText("摸鱼小猫");

    // Start a slow load
    vi.mocked(runtimeClient.getStickerLibrary).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveStaleLoad = resolve;
        }),
    );

    const refreshBtn = screen.getByRole("button", { name: "刷新表情库" });
    fireEvent.click(refreshBtn);

    // Now user deletes the sticker while refresh is in-flight
    const updatedSnapshotAfterDelete: StickerLibrarySnapshot = {
      ...sampleSnapshot,
      items: [],
    };
    vi.mocked(runtimeClient.deleteLearnedSticker).mockResolvedValueOnce({
      schema_version: "1.0",
      deleted: true,
      revision: 3,
    });
    // The reload after delete returns empty list
    vi.mocked(runtimeClient.getStickerLibrary).mockResolvedValueOnce(
      updatedSnapshotAfterDelete,
    );

    const deleteBtn = screen.getByRole("button", {
      name: "删除表情 摸鱼小猫",
    });
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(screen.getByText("暂无已学习的表情")).toBeTruthy();
    });

    // Stale slow load now resolves with old item "摸鱼小猫"
    resolveStaleLoad(sampleSnapshot);

    // Stale load MUST NOT overwrite the newer snapshot
    await waitFor(() => {
      expect(screen.queryByText("摸鱼小猫")).toBeNull();
      expect(screen.getByText("暂无已学习的表情")).toBeTruthy();
    });
  });
});
