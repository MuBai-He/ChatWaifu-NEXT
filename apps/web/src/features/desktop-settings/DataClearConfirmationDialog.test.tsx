import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DataClearConfirmationDialog } from "./DataClearConfirmationDialog";
import { DataSettingsSection } from "./DataSettingsSection";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";

describe("DataClearConfirmationDialog", () => {
  afterEach(cleanup);

  it("requires scope acknowledgement and the exact typed phrase", async () => {
    const onConfirm = vi.fn().mockResolvedValue(true);
    const context = {
      data: {
        sessionId: "00000000-0000-4000-8000-000000000001",
        resetting: false,
        refreshMemories: vi.fn(),
      },
      resetConversationAndMemory: onConfirm,
    } as unknown as DesktopSettingsContext;
    render(<DataSettingsSection context={context} />);

    fireEvent.click(screen.getByRole("button", { name: "清除当前数据" }));
    expect(
      screen.getByRole("dialog", { name: "清除当前对话与记忆" }),
    ).toBeTruthy();
    expect(screen.getByText("API 密钥、插件、MCP 与渠道设置")).toBeTruthy();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "我已了解，继续" }));
    const finalButton = screen.getByRole("button", { name: "永久清除" });
    expect((finalButton as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("输入“清除当前数据”"), {
      target: { value: "清除当前数据" },
    });
    expect((finalButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(finalButton);

    await waitFor(() => expect(onConfirm).toHaveBeenCalledOnce());
    expect(
      screen.queryByRole("dialog", { name: "清除当前对话与记忆" }),
    ).toBeNull();
  });

  it("stays open when Runtime rejects the clear operation", async () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn().mockResolvedValue(false);
    render(
      <DataClearConfirmationDialog
        open
        busy={false}
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "我已了解，继续" }));
    fireEvent.change(screen.getByLabelText("输入“清除当前数据”"), {
      target: { value: "清除当前数据" },
    });
    fireEvent.click(screen.getByRole("button", { name: "永久清除" }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledOnce());
    expect(onCancel).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "清除当前对话与记忆" }),
    ).toBeTruthy();
  });
});
