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

  it("renders metadata panel in preview dialog when photo has EXIF metadata", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValueOnce({
      schema_version: "1.0",
      settings: { schema_version: "1.0", retention_enabled: true, revision: 1 },
      items: [
        {
          schema_version: "1.0",
          photo_id: "photo-meta",
          sha256: "hash123",
          mime_type: "image/jpeg",
          byte_size: 204800,
          width: 800,
          height: 600,
          title: "风景照",
          description: "雪山和绿树",
          confidence: 0.95,
          keywords: ["雪山"],
          caption: "去年的旅行",
          user_annotations: [
            {
              annotation_id: "note-old",
              quote: "旧说明",
              kind: "date",
              source_generation_id: "gen-1",
              observed_at: "2024-01-01T00:00:00Z",
              superseded: true,
            },
            {
              annotation_id: "note-new",
              quote: "前年生日拍的",
              kind: "date",
              source_generation_id: "gen-2",
              observed_at: "2024-01-02T00:00:00Z",
              superseded: false,
            },
          ],
          received_at: "2024-05-10T12:00:00Z",
          saved_at: "2024-05-10T12:01:00Z",
          source_connection_id: "conn-1",
          source_session_id: "sess-1",
          source_turn_id: "turn-1",
          source_generation_id: "gen-1",
          captured_at: "2023-10-15T14:30:00+08:00",
          captured_at_offset: "+08:00",
          original_width: 4032,
          original_height: 3024,
          original_mime_type: "image/jpeg",
        },
      ],
      total_bytes: 204800,
      capacity: 200,
    });
    vi.mocked(fetchPhotoImageUrl).mockResolvedValue("blob:meta-url");

    render(<PhotoMemoryPanel characterId="default" runtimeOnline={true} />);

    await waitFor(() => {
      expect(screen.getByRole("img", { name: "风景照" })).toBeTruthy();
    });

    // Tile displays capture date with 拍摄: prefix
    expect(screen.getByText(/拍摄:/)).toBeTruthy();

    // Open dialog
    fireEvent.click(screen.getByRole("img", { name: "风景照" }));

    await waitFor(() => {
      expect(screen.getByTestId("dialog-captured-at")).toBeTruthy();
    });

    // Verify metadata list in dialog
    expect(screen.getByTestId("dialog-captured-at").textContent).toContain(
      "2023-10-15 14:30:00 (UTC+08:00)",
    );
    expect(screen.getByTestId("dialog-received-at").textContent).toBeTruthy();
    expect(screen.getByTestId("dialog-saved-at").textContent).toBeTruthy();
    expect(screen.getByTestId("dialog-original-specs").textContent).toBe(
      "4032 × 3024 (JPEG)",
    );
    expect(screen.getByTestId("dialog-stored-specs").textContent).toContain(
      "800 × 600 (JPEG)",
    );
    expect(screen.getByText('用户描述: "去年的旅行"')).toBeTruthy();
    expect(screen.getByText(/你补充的说明：前年生日拍的/)).toBeTruthy();
    expect(screen.queryByText(/旧说明/)).toBeNull();
  });

  it("handles photo without EXIF metadata gracefully with unknown defaults", async () => {
    vi.mocked(getPhotoMemory).mockResolvedValueOnce({
      schema_version: "1.0",
      settings: { schema_version: "1.0", retention_enabled: true, revision: 1 },
      items: [
        {
          schema_version: "1.0",
          photo_id: "photo-no-exif",
          sha256: "hash456",
          mime_type: "image/png",
          byte_size: 51200,
          width: 500,
          height: 400,
          title: "截图",
          description: "代码编辑器截图",
          confidence: 0.9,
          keywords: ["代码"],
          caption: "",
          received_at: "2024-06-01T08:00:00Z",
          saved_at: "2024-06-01T08:00:05Z",
          source_connection_id: "conn-1",
          source_session_id: "sess-1",
          source_turn_id: "turn-1",
          source_generation_id: "gen-1",
          // captured_at and original specs are null/undefined
        },
      ],
      total_bytes: 51200,
      capacity: 200,
    });
    vi.mocked(fetchPhotoImageUrl).mockResolvedValue("blob:no-exif-url");

    render(<PhotoMemoryPanel characterId="default" runtimeOnline={true} />);

    await waitFor(() => {
      expect(screen.getByRole("img", { name: "截图" })).toBeTruthy();
    });

    // Open dialog
    fireEvent.click(screen.getByRole("img", { name: "截图" }));

    await waitFor(() => {
      expect(screen.getByTestId("dialog-captured-at")).toBeTruthy();
    });

    expect(screen.getByTestId("dialog-captured-at").textContent).toBe("未知");
    expect(screen.getByTestId("dialog-original-specs").textContent).toBe(
      "未知",
    );
    expect(screen.getByTestId("dialog-stored-specs").textContent).toContain(
      "500 × 400 (PNG)",
    );
  });

  describe("formatCaptureDate", () => {
    it("returns 未知 for null or undefined input", async () => {
      const { formatCaptureDate } = await import("./photoDateUtils");
      expect(formatCaptureDate(null)).toBe("未知");
      expect(formatCaptureDate(undefined)).toBe("未知");
    });

    it("preserves naive datetime and marks timezone as unknown explicitly without timezone conversion", async () => {
      const { formatCaptureDate } = await import("./photoDateUtils");
      // Naive ISO string: must not be interpreted via new Date() which assumes host local time!
      expect(formatCaptureDate("2023-10-15T14:30:00", null)).toBe(
        "2023-10-15 14:30:00 (时区未知)",
      );
    });

    it("formats datetime with explicit timezone offset cleanly", async () => {
      const { formatCaptureDate } = await import("./photoDateUtils");
      expect(formatCaptureDate("2023-10-15T14:30:00+08:00", "+08:00")).toBe(
        "2023-10-15 14:30:00 (UTC+08:00)",
      );
    });
  });
});
