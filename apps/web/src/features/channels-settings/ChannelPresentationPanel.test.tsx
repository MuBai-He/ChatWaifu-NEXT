import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseChannelConnectionSnapshot } from "@chatwaifu/protocol";
import * as client from "../chat/runtimeClient";
import { setRemoteRuntimeConnection } from "../chat/runtimeEndpoint";
import { ChannelPresentationPanel } from "./ChannelPresentationPanel";

vi.mock("../chat/runtimeClient", () => ({ updateChannelConnection: vi.fn() }));
function connection() {
  return parseChannelConnectionSnapshot({
    configuration: {
      connection_id: "00000000-0000-4000-8000-000000000001",
      provider_id: "weixin_ilink",
      name: "我的微信",
      character_id: "default",
      principal_scope: "local",
      account_key: "account",
      allowed_sender_keys: ["owner"],
    },
    revision: 3,
    status: "ready",
    capabilities: {
      chat_types: ["direct"],
      outbound_message_kinds: ["text", "image"],
      inbound_message_kinds: ["text"],
      supports_typing: true,
    },
    created_at: "2026-10-06T00:00:00Z",
    updated_at: "2026-10-06T00:00:00Z",
  });
}
function open() {
  fireEvent.click(screen.getByText("回复样式与发送节奏"));
}

describe("channel presentation settings", () => {
  const onSaved = vi.fn();
  const onRefresh = vi.fn();
  beforeEach(() => {
    vi.mocked(client.updateChannelConnection).mockImplementation(
      (_id, configuration, revision) =>
        Promise.resolve({
          ...connection(),
          configuration,
          revision: revision + 1,
        }),
    );
  });
  afterEach(() => {
    cleanup();
    setRemoteRuntimeConnection(null);
    vi.resetAllMocks();
  });

  it("uses Runtime IM defaults and saves the complete policy while preserving routing", async () => {
    render(
      <ChannelPresentationPanel
        connection={connection()}
        editable
        onSaved={onSaved}
        onRefresh={onRefresh}
      />,
    );
    open();
    expect(
      screen.getByLabelText<HTMLInputElement>("建议每段字符数").value,
    ).toBe("30");
    expect(screen.getByLabelText<HTMLSelectElement>("回复形式").value).toBe(
      "instant_message",
    );
    const typing = screen.getByRole<HTMLInputElement>("switch", {
      name: "显示正在输入",
    });
    await waitFor(() => expect(typing.disabled).toBe(false));
    expect(
      screen.getByLabelText<HTMLInputElement>("打字速度（字/秒）").value,
    ).toBe("8");
    fireEvent.change(screen.getByLabelText("打字速度（字/秒）"), {
      target: { value: "6" },
    });
    fireEvent.change(screen.getByLabelText("随机停顿上限（毫秒）"), {
      target: { value: "900" },
    });
    fireEvent.click(typing);
    fireEvent.change(screen.getByLabelText("连接名称"), {
      target: { value: "新微信" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存回复样式" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(client.updateChannelConnection).toHaveBeenCalledWith(
      connection().configuration.connection_id,
      expect.objectContaining({
        ...connection().configuration,
        name: "新微信",
        timeout_seconds: 120,
        presentation_policy: expect.objectContaining({
          profile: "instant_message",
          max_parts: 3,
          preferred_chars_per_part: 30,
          soft_max_chars_per_part: 60,
          typing_enabled: true,
          typing_chars_per_second: 6,
          pause_jitter_ms: 900,
        }) as unknown,
      }),
      3,
      expect.any(AbortSignal) as unknown,
      expect.objectContaining({
        expectedContext: expect.any(Object) as unknown,
      }),
    );
  });

  it("rejects missing numbers and crossed length limits before writing", () => {
    render(
      <ChannelPresentationPanel
        connection={connection()}
        editable
        onSaved={onSaved}
        onRefresh={onRefresh}
      />,
    );
    open();
    fireEvent.change(screen.getByLabelText("每段柔性上限"), {
      target: { value: "20" },
    });
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "保存回复样式" })
        .disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("每段柔性上限"), {
      target: { value: "" },
    });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(client.updateChannelConnection).not.toHaveBeenCalled();
  });

  it("disables unsupported typing and preserves single-text without silently enabling IM", async () => {
    const conn = connection();
    conn.configuration.presentation_policy = {
      profile: "single_text",
      stickers_enabled: true,
    };
    conn.capabilities!.supports_typing = false;
    render(
      <ChannelPresentationPanel
        connection={conn}
        editable
        onSaved={onSaved}
        onRefresh={onRefresh}
      />,
    );
    open();
    expect(
      screen.getByRole<HTMLInputElement>("switch", { name: "显示正在输入" })
        .disabled,
    ).toBe(true);
    expect(
      screen
        .getByLabelText<HTMLInputElement>("建议每段字符数")
        .matches(":disabled"),
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("连接名称"), {
      target: { value: "新微信" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "保存回复样式" })
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存回复样式" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(
      vi.mocked(client.updateChannelConnection).mock.calls[0][1]
        .presentation_policy?.profile,
    ).toBe("single_text");
  });

  it("requests a fresh read after uncertain writes without auto-retry", async () => {
    vi.mocked(client.updateChannelConnection).mockRejectedValue(
      new Error("conflict"),
    );
    render(
      <ChannelPresentationPanel
        connection={connection()}
        editable
        onSaved={onSaved}
        onRefresh={onRefresh}
      />,
    );
    open();
    fireEvent.change(screen.getByLabelText("连接名称"), {
      target: { value: "新微信" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "保存回复样式" })
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存回复样式" }));
    await screen.findByText(/保存结果未确认/u);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
    expect(client.updateChannelConnection).toHaveBeenCalledTimes(1);
  });

  it("aborts an old save and ignores its late result when Runtime changes", async () => {
    setRemoteRuntimeConnection({ baseUrl: "https://a.example", token: "a" });
    let finish!: (value: ReturnType<typeof connection>) => void;
    vi.mocked(client.updateChannelConnection).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(
      <ChannelPresentationPanel
        connection={connection()}
        editable
        onSaved={onSaved}
        onRefresh={onRefresh}
      />,
    );
    open();
    fireEvent.change(screen.getByLabelText("连接名称"), {
      target: { value: "新微信" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "保存回复样式" })
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存回复样式" }));
    await waitFor(() =>
      expect(client.updateChannelConnection).toHaveBeenCalledTimes(1),
    );
    const call = vi.mocked(client.updateChannelConnection).mock.calls[0];
    act(() =>
      setRemoteRuntimeConnection({ baseUrl: "https://b.example", token: "b" }),
    );
    await act(() => Promise.resolve(finish(connection())));
    expect(call[3]?.aborted).toBe(true);
    expect(call[4]?.expectedContext?.connection.baseUrl).toBe(
      "https://a.example",
    );
    expect(onSaved).not.toHaveBeenCalled();
  });
});
