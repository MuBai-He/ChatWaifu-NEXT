import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getStickerUsage,
  type StickerUsageHistory,
} from "../chat/runtimeClient";
import { StickerUsagePanel } from "./StickerUsagePanel";

vi.mock("../chat/runtimeClient", () => ({ getStickerUsage: vi.fn() }));

const history = {
  schema_version: "1.0",
  scan_limit: 200,
  has_more: true,
  items: [
    {
      schema_version: "1.0",
      part_id: "00000000-0000-4000-8000-000000000001",
      sticker_id: "kitten_happy",
      label: "开心小猫",
      origin: "preset",
      status: "delivered",
      attempt: 2,
      created_at: "2026-09-07T01:00:00Z",
      updated_at: "2026-09-07T01:00:01Z",
      delivered_at: "2026-09-07T01:00:01Z",
    },
  ],
} satisfies StickerUsageHistory;

async function expand() {
  fireEvent.click(screen.getByText("最近表情发送记录"));
  await waitFor(() => expect(getStickerUsage).toHaveBeenCalled());
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("StickerUsagePanel", () => {
  it("loads on demand and distinguishes failed image outcomes from delivered usage", async () => {
    vi.mocked(getStickerUsage).mockResolvedValue({
      ...history,
      items: [
        history.items[0],
        {
          ...history.items[0],
          part_id: "00000000-0000-4000-8000-000000000002",
          label: "害羞小猫",
          status: "failed",
          delivered_at: null,
          attempt: 1,
        },
      ],
    });
    render(
      <StickerUsagePanel
        characterId="default"
        runtimeOnline
        refreshToken={{}}
      />,
    );
    expect(getStickerUsage).not.toHaveBeenCalled();
    await expand();
    expect(await screen.findByText("已送达")).toBeTruthy();
    expect(screen.getByText("发送失败")).toBeTruthy();
    expect(screen.getByText("尝试 2 次")).toBeTruthy();
    expect(screen.getByText("这里只显示最近保留的一部分记录。")).toBeTruthy();
  });

  it("aborts stale loads when the library changes and does not restore deleted labels", async () => {
    let resolveOld!: (value: StickerUsageHistory) => void;
    vi.mocked(getStickerUsage).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    const view = render(
      <StickerUsagePanel
        characterId="default"
        runtimeOnline
        refreshToken={{}}
      />,
    );
    await expand();
    const oldSignal = vi.mocked(getStickerUsage).mock.calls[0][1];
    vi.mocked(getStickerUsage).mockResolvedValue({
      ...history,
      items: [],
      has_more: false,
    });
    view.rerender(
      <StickerUsagePanel
        characterId="default"
        runtimeOnline
        refreshToken={{}}
      />,
    );
    expect(await screen.findByText("暂无可显示的发送记录。")).toBeTruthy();
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => {
      resolveOld(history);
      await Promise.resolve();
    });
    expect(screen.queryByText("开心小猫")).toBeNull();
  });

  it("allows retry after failure and clears history when offline", async () => {
    const token = {};
    vi.mocked(getStickerUsage).mockRejectedValueOnce(new Error("unavailable"));
    const view = render(
      <StickerUsagePanel
        characterId="default"
        runtimeOnline
        refreshToken={token}
      />,
    );
    await expand();
    expect(await screen.findByRole("alert")).toBeTruthy();
    vi.mocked(getStickerUsage).mockResolvedValue(history);
    fireEvent.click(screen.getByRole("button", { name: "刷新发送记录" }));
    expect(await screen.findByText("开心小猫")).toBeTruthy();
    view.rerender(
      <StickerUsagePanel
        characterId="default"
        runtimeOnline={false}
        refreshToken={token}
      />,
    );
    expect(screen.getByText("连接后可查看发送记录。")).toBeTruthy();
    expect(screen.queryByText("开心小猫")).toBeNull();
  });
});
