import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseChannelRuntimeSettingsResponse } from "@chatwaifu/protocol";
import * as client from "../chat/runtime-client/channelSettingsClient";
import { RuntimeRequestError } from "../chat/runtime-client/http";
import { setRemoteRuntimeConnection } from "../chat/runtimeEndpoint";
import { ChannelRuntimeSettingsPanel } from "./ChannelRuntimeSettingsPanel";

vi.mock("../chat/runtime-client/channelSettingsClient", () => ({
  getChannelRuntimeSettings: vi.fn(),
  updateChannelRuntimeSettings: vi.fn(),
}));

function snapshot(
  revision = 3,
  input_tokens = 1536,
  qq_owner_public_web_enabled = false,
) {
  return parseChannelRuntimeSettingsResponse({
    revision,
    policy: {
      qq_owner_public_web_enabled,
      group_discussion: { input_tokens },
    },
    updated_at: "2026-10-06T00:00:00Z",
    search_provider: "searxng",
    reader_provider: "crawl4ai",
    stt_provider: "disabled",
  });
}
async function ready() {
  return screen.findByRole<HTMLInputElement>("switch", {
    name: "允许 QQ 主人私聊联网查资料",
  });
}

describe("Runtime channel settings", () => {
  beforeEach(() => {
    vi.mocked(client.getChannelRuntimeSettings).mockResolvedValue(snapshot());
  });
  afterEach(() => {
    cleanup();
    setRemoteRuntimeConnection(null);
    vi.resetAllMocks();
  });

  it("loads service names and all bounded fields, saves CAS and retains unedited values", async () => {
    vi.mocked(client.updateChannelRuntimeSettings).mockImplementation(
      (policy, revision) =>
        Promise.resolve({ ...snapshot(revision + 1), policy }),
    );
    render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    fireEvent.click(await ready());
    fireEvent.change(screen.getByLabelText("旁听输入预算（参考 token）"), {
      target: { value: "4096" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存渠道权限与预算" }));
    await screen.findByText(/渠道设置已保存到当前 Runtime/u);
    expect(client.updateChannelRuntimeSettings).toHaveBeenCalledWith(
      {
        ...snapshot().policy,
        qq_owner_public_web_enabled: true,
        group_discussion: {
          ...snapshot().policy.group_discussion,
          input_tokens: 4096,
        },
      },
      3,
      expect.objectContaining({
        signal: expect.any(AbortSignal) as unknown,
        expectedContext: expect.any(Object) as unknown,
      }),
    );
    expect(screen.getByText(/searxng \/ crawl4ai/u)).toBeTruthy();
    expect(
      screen.getByText(/未配置，请先在后端配置语音识别服务/u),
    ).toBeTruthy();
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "保存渠道权限与预算",
      }).disabled,
    ).toBe(true);
  });

  it("saves QQ account authority independently of owner agent and public web", async () => {
    vi.mocked(client.updateChannelRuntimeSettings).mockImplementation(
      (policy, revision) =>
        Promise.resolve({ ...snapshot(revision + 1), policy }),
    );
    render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    fireEvent.click(
      await screen.findByRole("switch", { name: "将 QQ 作为角色自己的账号" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存渠道权限与预算" }));
    await screen.findByText(/渠道设置已保存到当前 Runtime/u);
    expect(client.updateChannelRuntimeSettings).toHaveBeenCalledWith(
      { ...snapshot().policy, qq_account_enabled: true },
      3,
      expect.any(Object),
    );
  });

  it.each(["", "127", "8193"])(
    "rejects invalid input budget %j before any mutation",
    async (value) => {
      render(<ChannelRuntimeSettingsPanel runtimeOnline />);
      await ready();
      fireEvent.change(screen.getByLabelText("旁听输入预算（参考 token）"), {
        target: { value },
      });
      expect(
        screen.getByRole<HTMLButtonElement>("button", {
          name: "保存渠道权限与预算",
        }).disabled,
      ).toBe(true);
      expect(screen.getByRole("alert")).toBeTruthy();
      expect(client.updateChannelRuntimeSettings).not.toHaveBeenCalled();
    },
  );

  it("saves free conversation without widening incoming or owner permissions", async () => {
    vi.mocked(client.updateChannelRuntimeSettings).mockImplementation(
      (policy, revision) =>
        Promise.resolve({ ...snapshot(revision + 1), policy }),
    );
    render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    fireEvent.click(await screen.findByRole("switch", { name: "QQ 自由交流" }));
    fireEvent.click(screen.getByRole("button", { name: "保存渠道权限与预算" }));
    await screen.findByText(/渠道设置已保存到当前 Runtime/u);
    expect(client.updateChannelRuntimeSettings).toHaveBeenCalledWith(
      { ...snapshot().policy, qq_free_chat_enabled: true },
      3,
      expect.any(Object),
    );
  });

  it("refuses a member quota larger than group capacity", async () => {
    render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    await ready();
    fireEvent.change(screen.getByLabelText("每群缓存条数"), {
      target: { value: "32" },
    });
    fireEvent.change(screen.getByLabelText("每人缓存条数"), {
      target: { value: "33" },
    });
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "保存渠道权限与预算",
      }).disabled,
    ).toBe(true);
  });

  it("reads back a conflict, discards the losing draft and never auto-retries a write", async () => {
    vi.mocked(client.updateChannelRuntimeSettings).mockRejectedValue(
      new RuntimeRequestError("conflict", 409),
    );
    render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    fireEvent.click(await ready());
    vi.mocked(client.getChannelRuntimeSettings).mockResolvedValue(
      snapshot(4, 4096),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存渠道权限与预算" }));
    await screen.findByText(/不会自动重试修改/u);
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("旁听输入预算（参考 token）")
          .value,
      ).toBe("4096"),
    );
    expect((await ready()).checked).toBe(false);
    expect(client.updateChannelRuntimeSettings).toHaveBeenCalledTimes(1);
    expect(client.getChannelRuntimeSettings).toHaveBeenCalledTimes(2);
  });

  it("disables unsupported APIs instead of displaying editable invented defaults", async () => {
    vi.mocked(client.getChannelRuntimeSettings).mockRejectedValue(
      new RuntimeRequestError("missing", 404),
    );
    render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    await screen.findByText(/此 Runtime 尚未提供渠道高级设置接口/u);
    expect(screen.queryByRole("switch")).toBeNull();
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "保存渠道权限与预算",
      }).disabled,
    ).toBe(true);
    expect(client.updateChannelRuntimeSettings).not.toHaveBeenCalled();
  });

  it("aborts pending reads while offline and requires a fresh read on reconnection", async () => {
    const view = render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    const toggle = await ready();
    const signal = vi.mocked(client.getChannelRuntimeSettings).mock.calls[0][0]
      .signal;
    view.rerender(<ChannelRuntimeSettingsPanel runtimeOnline={false} />);
    expect(signal.aborted).toBe(true);
    expect(toggle.disabled).toBe(true);
    fireEvent.click(toggle);
    expect(client.updateChannelRuntimeSettings).not.toHaveBeenCalled();
    vi.mocked(client.getChannelRuntimeSettings).mockResolvedValue(
      snapshot(4, 4096, true),
    );
    view.rerender(<ChannelRuntimeSettingsPanel runtimeOnline />);
    await waitFor(() => expect(toggle.checked && !toggle.disabled).toBe(true));
    expect(
      screen.getByLabelText<HTMLInputElement>("旁听输入预算（参考 token）")
        .value,
    ).toBe("4096");
  });

  it("ignores a late Runtime A save after Runtime B is loaded", async () => {
    setRemoteRuntimeConnection({ baseUrl: "https://a.example", token: "a" });
    let finish!: (value: ReturnType<typeof snapshot>) => void;
    vi.mocked(client.updateChannelRuntimeSettings).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<ChannelRuntimeSettingsPanel runtimeOnline />);
    fireEvent.click(await ready());
    fireEvent.click(screen.getByRole("button", { name: "保存渠道权限与预算" }));
    await waitFor(() =>
      expect(client.updateChannelRuntimeSettings).toHaveBeenCalledTimes(1),
    );
    const options = vi.mocked(client.updateChannelRuntimeSettings).mock
      .calls[0][2];
    vi.mocked(client.getChannelRuntimeSettings).mockResolvedValue(
      snapshot(8, 3072),
    );
    act(() =>
      setRemoteRuntimeConnection({ baseUrl: "https://b.example", token: "b" }),
    );
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("旁听输入预算（参考 token）")
          .value,
      ).toBe("3072"),
    );
    await act(() => Promise.resolve(finish(snapshot(4, 4096, true))));
    expect(options.signal.aborted).toBe(true);
    expect(options.expectedContext.connection.baseUrl).toBe(
      "https://a.example",
    );
    expect((await ready()).checked).toBe(false);
    expect(screen.queryByText(/渠道设置已保存/u)).toBeNull();
    expect(
      screen.getByLabelText<HTMLInputElement>("旁听输入预算（参考 token）")
        .value,
    ).toBe("3072");
  });
});
