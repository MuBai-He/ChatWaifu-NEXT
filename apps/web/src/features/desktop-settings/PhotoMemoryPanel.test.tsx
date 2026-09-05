// @vitest-environment jsdom
import {
  render,
  screen,
  waitFor,
  fireEvent,
  cleanup,
} from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { PhotoMemoryPanel } from "./PhotoMemoryPanel";
import {
  getPhotoMemory,
  updatePhotoMemorySettings,
  deleteSavedPhoto,
  fetchPhotoImageUrl,
} from "../chat/runtimeClient";

vi.mock("../chat/runtimeClient", () => ({
  getPhotoMemory: vi.fn(),
  updatePhotoMemorySettings: vi.fn(),
  deleteSavedPhoto: vi.fn(),
  fetchPhotoImageUrl: vi.fn(),
}));

describe("PhotoMemoryPanel", () => {
  const revokeObjectURL = vi.fn();
  beforeEach(() => {
    vi.resetAllMocks();

    vi.stubGlobal("IntersectionObserver", undefined);
    window.URL.createObjectURL = vi.fn(() => "blob:fake-url");
    window.URL.revokeObjectURL = revokeObjectURL;
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows empty state when no photos exist", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValue({
      schema_version: "1.0",
      settings: {
        schema_version: "1.0",
        retention_enabled: false,
        revision: 1,
      },
      items: [],
      total_bytes: 0,
      capacity: 200,
    });
    render(<PhotoMemoryPanel characterId="default" runtimeOnline={true} />);

    expect(screen.getByText("正在加载照片记忆…")).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("暂无已保存的照片")).toBeTruthy();
    });

    const checkbox = screen.getByRole<HTMLInputElement>("switch", {
      name: /记住我发的照片/i,
    });
    expect(checkbox.checked).toBe(false);
  });

  it("handles settings CAS 409 conflict and retries", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValue({
      schema_version: "1.0",
      settings: {
        schema_version: "1.0",
        retention_enabled: false,
        revision: 1,
      },
      items: [],
      total_bytes: 0,
      capacity: 200,
    });

    const error409 = new Error("409 Conflict");
    vi.mocked(updatePhotoMemorySettings).mockRejectedValueOnce(error409);

    render(<PhotoMemoryPanel characterId="default" runtimeOnline={true} />);

    await waitFor(() => {
      expect(screen.getByRole<HTMLInputElement>("switch").disabled).toBe(false);
    });

    vi.mocked(getPhotoMemory).mockResolvedValue({
      schema_version: "1.0",
      settings: {
        schema_version: "1.0",
        retention_enabled: false,
        revision: 2,
      },
      items: [],
      total_bytes: 0,
      capacity: 200,
    });

    fireEvent.click(screen.getByRole<HTMLInputElement>("switch"));

    await waitFor(() => {
      expect(screen.getByText("409 Conflict")).toBeTruthy();
    });

    expect(getPhotoMemory).toHaveBeenCalledTimes(2); // Initial load + conflict retry
  });

  it("handles delete", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValueOnce({
      schema_version: "1.0",
      settings: { schema_version: "1.0", retention_enabled: true, revision: 1 },
      items: [
        {
          schema_version: "1.0",
          photo_id: "photo-1",
          sha256: "hash",
          mime_type: "image/png",
          byte_size: 100,
          width: 100,
          height: 100,
          title: "Title 1",
          description: "Desc 1",
          confidence: 0.9,
          keywords: [],
          caption: "",
          received_at: "2026-09-01T12:00:00Z",
          saved_at: "2026-09-01T12:00:00Z",
          source_connection_id: "conn-1",
          source_session_id: "sess-1",
          source_turn_id: "turn-1",
          source_generation_id: "gen-1",
        },
      ],
      total_bytes: 100,
      capacity: 200,
    });

    render(<PhotoMemoryPanel characterId="default" runtimeOnline={true} />);

    await waitFor(() => {
      expect(screen.getByText("Title 1")).toBeTruthy();
    });

    vi.mocked(deleteSavedPhoto).mockResolvedValue({
      schema_version: "1.0",
      deleted: true,
      revision: 2,
    });

    vi.mocked(getPhotoMemory).mockResolvedValueOnce({
      schema_version: "1.0",
      settings: { schema_version: "1.0", retention_enabled: true, revision: 2 },
      items: [],
      total_bytes: 0,
      capacity: 200,
    });

    fireEvent.click(screen.getByRole("button", { name: "删除照片 Title 1" }));

    await waitFor(() => {
      expect(screen.getByText("暂无已保存的照片")).toBeTruthy();
    });
  });

  it("delete refresh failure hides previous photo immediately", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValueOnce({
      schema_version: "1.0",
      settings: { schema_version: "1.0", retention_enabled: true, revision: 1 },
      items: [
        {
          schema_version: "1.0",
          photo_id: "photo-1",
          sha256: "hash",
          mime_type: "image/png",
          byte_size: 100,
          width: 100,
          height: 100,
          title: "Title 1",
          description: "Desc 1",
          confidence: 0.9,
          keywords: [],
          caption: "",
          received_at: "2026-09-01T12:00:00Z",
          saved_at: "2026-09-01T12:00:00Z",
          source_connection_id: "conn-1",
          source_session_id: "sess-1",
          source_turn_id: "turn-1",
          source_generation_id: "gen-1",
        },
      ],
      total_bytes: 100,
      capacity: 200,
    });

    render(<PhotoMemoryPanel characterId="default" runtimeOnline={true} />);

    await waitFor(() => {
      expect(screen.getByText("Title 1")).toBeTruthy();
    });

    vi.mocked(deleteSavedPhoto).mockResolvedValue({
      schema_version: "1.0",
      deleted: true,
      revision: 2,
    });

    vi.mocked(getPhotoMemory).mockRejectedValueOnce(
      new Error("Refresh failed"),
    );

    fireEvent.click(screen.getByRole("button", { name: "删除照片 Title 1" }));

    await waitFor(() => {
      expect(screen.queryByText("Title 1")).not.toBeTruthy();
    });
  });

  it("preview dialog open close", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValueOnce({
      schema_version: "1.0",
      settings: { schema_version: "1.0", retention_enabled: true, revision: 1 },
      items: [
        {
          schema_version: "1.0",
          photo_id: "photo-1",
          sha256: "hash",
          mime_type: "image/png",
          byte_size: 100,
          width: 100,
          height: 100,
          title: "Title 1",
          description: "Desc 1",
          confidence: 0.9,
          keywords: [],
          caption: "",
          received_at: "2026-09-01T12:00:00Z",
          saved_at: "2026-09-01T12:00:00Z",
          source_connection_id: "conn-1",
          source_session_id: "sess-1",
          source_turn_id: "turn-1",
          source_generation_id: "gen-1",
        },
      ],
      total_bytes: 100,
      capacity: 200,
    });
    vi.mocked(fetchPhotoImageUrl).mockResolvedValue("blob:fake-url");

    render(<PhotoMemoryPanel characterId="default" runtimeOnline={true} />);

    await waitFor(() => {
      expect(screen.getByRole("img", { name: "Title 1" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("img", { name: "Title 1" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "关闭照片预览" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "关闭照片预览" }));

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "关闭照片预览" }),
      ).not.toBeTruthy();
    });
  });

  it("cleans up URL object and aborts on unmount", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValueOnce({
      schema_version: "1.0",
      settings: { schema_version: "1.0", retention_enabled: true, revision: 1 },
      items: [
        {
          schema_version: "1.0",
          photo_id: "photo-1",
          sha256: "hash",
          mime_type: "image/png",
          byte_size: 100,
          width: 100,
          height: 100,
          title: "Title 1",
          description: "Desc 1",
          confidence: 0.9,
          keywords: [],
          caption: "",
          received_at: "2026-09-01T12:00:00Z",
          saved_at: "2026-09-01T12:00:00Z",
          source_connection_id: "conn-1",
          source_session_id: "sess-1",
          source_turn_id: "turn-1",
          source_generation_id: "gen-1",
        },
      ],
      total_bytes: 100,
      capacity: 200,
    });
    vi.mocked(fetchPhotoImageUrl).mockResolvedValue("blob:fake-url");

    const { unmount } = render(
      <PhotoMemoryPanel characterId="default" runtimeOnline={true} />,
    );

    await waitFor(() => {
      expect(screen.getByRole("img", { name: "Title 1" })).toBeTruthy();
    });

    unmount();

    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake-url");
  });
});
