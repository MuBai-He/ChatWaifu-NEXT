import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as runtimeClient from "../chat/runtimeClient";
import type {
  ChannelAuthorizationSnapshot,
  ChannelConnectionSnapshot,
} from "../chat/runtimeClient";
import type { DesktopSettingsContext } from "../desktop-settings/DesktopSettingsContext";
import { ChannelsSettingsSection } from "./ChannelsSettingsSection";
import { setRemoteRuntimeConnection } from "../chat/runtimeEndpoint";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value }: { value: string }) => (
    <svg data-testid="weixin-qr" data-value={value} />
  ),
}));

vi.mock("../chat/runtimeClient", () => ({
  cancelChannelAuthorization: vi.fn(),
  deleteChannelConnection: vi.fn(),
  deleteLearnedSticker: vi.fn(),
  fetchStickerImageUrl: vi.fn(),
  getChannelAuthorization: vi.fn(),
  getChannelConnections: vi.fn(),
  getStickerLibrary: vi.fn(),
  getPhotoMemory: vi.fn(),
  startChannelAuthorization: vi.fn(),
  submitChannelAuthorizationVerification: vi.fn(),
  updateChannelConnection: vi.fn(),
  updateChannelPresentationPolicy: vi.fn(),
  updateStickerLibrarySettings: vi.fn(),
}));

describe("ChannelsSettingsSection", () => {
  beforeEach(() => {
    vi.mocked(runtimeClient.getPhotoMemory).mockResolvedValue({
      settings: { retention_enabled: false, revision: 0 },
      items: [],
      total_bytes: 0,
      capacity: 200,
    });
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([]);
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
    vi.mocked(runtimeClient.fetchStickerImageUrl).mockResolvedValue(
      "blob:http://localhost/sticker",
    );
    vi.mocked(runtimeClient.cancelChannelAuthorization).mockResolvedValue();
    vi.mocked(runtimeClient.deleteChannelConnection).mockResolvedValue();
    vi.mocked(
      runtimeClient.submitChannelAuthorizationVerification,
    ).mockResolvedValue(authorization("scanned"));
    vi.mocked(runtimeClient.updateChannelPresentationPolicy).mockImplementation(
      (conn, policy) =>
        Promise.resolve({
          ...conn,
          revision: conn.revision + 1,
          configuration: {
            ...conn.configuration,
            presentation_policy: policy,
          },
        }),
    );
  });

  afterEach(() => {
    cleanup();
    setRemoteRuntimeConnection(null);
    vi.clearAllMocks();
  });

  it("shows the selected Runtime address without credentials", async () => {
    setRemoteRuntimeConnection({
      baseUrl: "https://example.test/runtime?secret=hidden#private",
      token: "operator-secret",
    });
    render(<ChannelsSettingsSection context={context()} />);
    expect(
      await screen.findByText("https://example.test/runtime"),
    ).toBeTruthy();
    expect(screen.queryByText(/operator-secret|hidden|private/)).toBeNull();
  });

  it("starts native Weixin QR authorization without manual identity or secret fields", async () => {
    let pollingSignal: AbortSignal | undefined;
    vi.mocked(runtimeClient.startChannelAuthorization).mockResolvedValue(
      authorization("pending"),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockImplementation(
      (_id, _wait, signal) => {
        pollingSignal = signal;
        return cancellableWait(signal);
      },
    );

    const view = render(<ChannelsSettingsSection context={context()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "扫码绑定微信" }),
    );

    await waitFor(() =>
      expect(runtimeClient.startChannelAuthorization).toHaveBeenCalledWith(
        "weixin_ilink",
        "default",
        expect.any(AbortSignal) as unknown,
        expect.objectContaining({
          expectedContext: expect.any(Object) as unknown,
        }),
      ),
    );
    expect(
      (await screen.findByTestId("weixin-qr")).getAttribute("data-value"),
    ).toBe("weixin://pair/session-1");
    expect(screen.queryByLabelText("手机验证码")).toBeNull();
    expect(
      view.container.querySelectorAll('input:not([type="checkbox"])'),
    ).toHaveLength(0);
    // Retained photos stay manageable even when WeChat is disconnected.
    expect(screen.getByRole("switch", { name: "记住我发的照片" })).toBeTruthy();

    view.unmount();
    expect(pollingSignal?.aborted).toBe(true);
  });

  it("shows and submits a verification code only in verification_required state", async () => {
    vi.mocked(runtimeClient.startChannelAuthorization).mockResolvedValue(
      authorization("verification_required"),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockImplementation(
      (_id, _wait, signal) => cancellableWait(signal),
    );
    vi.mocked(
      runtimeClient.submitChannelAuthorizationVerification,
    ).mockResolvedValue(authorization("scanned"));

    render(<ChannelsSettingsSection context={context()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "扫码绑定微信" }),
    );

    const input = await screen.findByLabelText("手机验证码");
    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() =>
      expect(
        runtimeClient.submitChannelAuthorizationVerification,
      ).toHaveBeenCalledWith(
        "00000000-0000-4000-8000-000000000201",
        "123456",
        expect.any(AbortSignal) as unknown,
        expect.objectContaining({
          expectedContext: expect.any(Object) as unknown,
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByLabelText("手机验证码")).toBeNull(),
    );
  });

  it("promotes a confirmed poll result to a connected card", async () => {
    vi.mocked(runtimeClient.startChannelAuthorization).mockResolvedValue(
      authorization("pending"),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockResolvedValue(
      authorization("confirmed", connection()),
    );

    render(<ChannelsSettingsSection context={context()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "扫码绑定微信" }),
    );

    expect(await screen.findByText("我的微信")).toBeTruthy();
    expect(screen.getByText("已连接")).toBeTruthy();
    expect(screen.queryByTestId("weixin-qr")).toBeNull();
  });

  it("cancels an active authorization and disconnects an existing connection", async () => {
    vi.mocked(runtimeClient.startChannelAuthorization).mockResolvedValue(
      authorization("pending"),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockImplementation(
      (_id, _wait, signal) => cancellableWait(signal),
    );

    const view = render(<ChannelsSettingsSection context={context()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "扫码绑定微信" }),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockImplementation(
      (_id, wait, signal) =>
        wait === 0
          ? Promise.resolve(authorization("cancelled"))
          : cancellableWait(signal),
    );
    fireEvent.click(await screen.findByRole("button", { name: "取消绑定" }));
    await waitFor(() =>
      expect(runtimeClient.cancelChannelAuthorization).toHaveBeenCalledWith(
        "00000000-0000-4000-8000-000000000201",
        expect.objectContaining({
          signal: expect.any(AbortSignal) as unknown,
          expectedContext: expect.any(Object) as unknown,
        }),
      ),
    );
    expect(
      await screen.findByRole("button", { name: "扫码绑定微信" }),
    ).toBeTruthy();

    view.unmount();
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      connection(),
    ]);
    render(<ChannelsSettingsSection context={context()} />);
    fireEvent.click(await screen.findByRole("button", { name: "断开连接" }));
    expect(runtimeClient.deleteChannelConnection).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认解除微信绑定" }));
    await waitFor(() =>
      expect(runtimeClient.deleteChannelConnection).toHaveBeenCalledWith(
        "00000000-0000-4000-8000-000000000202",
        expect.objectContaining({
          signal: expect.any(AbortSignal) as unknown,
          expectedContext: expect.any(Object) as unknown,
        }),
      ),
    );
  });

  it("persists stickers opt-in toggle and preserves existing presentation policy", async () => {
    const existingConn = connection("ready", true, {
      profile: "instant_message",
      cadence_enabled: true,
      min_delay_ms: 800,
      max_delay_ms: 3000,
      stickers_enabled: false,
    });
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      existingConn,
    ]);

    render(<ChannelsSettingsSection context={context()} />);

    expect(await screen.findByText("合适的时候发送表情")).toBeTruthy();
    expect(screen.getByText("发送原创小猫和已学表情，默认关闭")).toBeTruthy();

    const toggle = screen.getByRole<HTMLInputElement>("switch", {
      name: "合适的时候发送表情",
    });
    expect(toggle.checked).toBe(false);
    expect(toggle.disabled).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() =>
      expect(
        runtimeClient.updateChannelPresentationPolicy,
      ).toHaveBeenCalledTimes(1),
    );
    expect(runtimeClient.updateChannelPresentationPolicy).toHaveBeenCalledWith(
      existingConn,
      expect.objectContaining({
        profile: "instant_message",
        cadence_enabled: true,
        min_delay_ms: 800,
        max_delay_ms: 3000,
        stickers_enabled: true,
      }),
      expect.any(AbortSignal) as unknown,
      expect.objectContaining({
        expectedContext: expect.any(Object) as unknown,
      }),
    );

    await waitFor(() => expect(toggle.checked).toBe(true));
  });

  it("handles presentation policy update errors and leaves previous state intact", async () => {
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      connection(),
    ]);
    vi.mocked(
      runtimeClient.updateChannelPresentationPolicy,
    ).mockRejectedValueOnce(new Error("网络连接失败"));

    render(<ChannelsSettingsSection context={context()} />);

    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "合适的时候发送表情",
    });
    expect(toggle.checked).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByRole("status")).toBeTruthy();
      expect(screen.getByText("网络连接失败")).toBeTruthy();
    });
    expect(toggle.checked).toBe(false);
  });

  it("disables stickers toggle for non-default characters with explanatory copy", async () => {
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      connection(),
    ]);

    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      {
        ...connection(),
        configuration: {
          ...connection().configuration,
          character_id: "custom_other_char",
        },
      },
    ]);
    render(<ChannelsSettingsSection context={context("custom_other_char")} />);

    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "合适的时候发送表情",
    });
    expect(toggle.disabled).toBe(true);
    expect(
      screen.getByText("发送原创小猫和已学表情，默认关闭（仅默认角色支持）"),
    ).toBeTruthy();
  });

  it("disables stickers toggle when connection has non instant_message profile without force enabling single_text", async () => {
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      connection("ready", true, {
        profile: "single_text",
        stickers_enabled: false,
      }),
    ]);

    render(<ChannelsSettingsSection context={context()} />);

    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "合适的时候发送表情",
    });
    expect(toggle.disabled).toBe(true);
    expect(
      screen.getByText(
        "发送原创小猫和已学表情，默认关闭（仅即时消息模式支持）",
      ),
    ).toBeTruthy();
    expect(
      runtimeClient.updateChannelPresentationPolicy,
    ).not.toHaveBeenCalled();
  });

  it("selects the ready binding, shows expired bindings truthfully and offers rebind without deleting", async () => {
    const old = {
      ...connection("error"),
      configuration: { ...connection().configuration, name: "旧微信" },
    };
    const fresh = {
      ...connection(),
      configuration: {
        ...connection().configuration,
        name: "新微信",
        connection_id: "00000000-0000-4000-8000-000000000203",
      },
    };
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      old,
      fresh,
    ]);
    vi.mocked(runtimeClient.startChannelAuthorization).mockResolvedValue(
      authorization("pending"),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockImplementation(
      (_id, _wait, signal) => cancellableWait(signal),
    );
    render(<ChannelsSettingsSection context={context()} />);
    expect(await screen.findByText("新微信", { selector: "h3" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("管理哪个微信绑定"), {
      target: { value: old.configuration.connection_id },
    });
    expect(screen.getByText("连接异常")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新扫码绑定" }));
    await screen.findByTestId("weixin-qr");
    expect(runtimeClient.deleteChannelConnection).not.toHaveBeenCalled();
  });

  it("restores a disabled binding through CAS while preserving identity and presentation", async () => {
    const saved = connection("disabled", false);
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([saved]);
    vi.mocked(runtimeClient.updateChannelConnection).mockImplementation(
      (_id, configuration) =>
        Promise.resolve({
          ...saved,
          revision: 2,
          status: "untested",
          configuration,
        }),
    );
    render(<ChannelsSettingsSection context={context()} />);
    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "启用微信消息",
    });
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle.checked).toBe(true));
    expect(runtimeClient.updateChannelConnection).toHaveBeenCalledWith(
      saved.configuration.connection_id,
      { ...saved.configuration, enabled: true },
      1,
      expect.any(AbortSignal) as unknown,
      expect.objectContaining({
        expectedContext: expect.any(Object) as unknown,
      }),
    );
    expect(screen.getByText("待检查")).toBeTruthy();
  });

  it("reconciles confirmation that won the race against cancellation", async () => {
    vi.mocked(runtimeClient.startChannelAuthorization).mockResolvedValue(
      authorization("pending"),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockImplementation(
      (_id, wait, signal) =>
        wait === 0
          ? Promise.resolve(authorization("confirmed", connection()))
          : cancellableWait(signal),
    );
    render(<ChannelsSettingsSection context={context()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "扫码绑定微信" }),
    );
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      connection(),
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "取消绑定" }));
    await screen.findByText("我的微信", { selector: "h3" });
    expect(screen.queryByTestId("weixin-qr")).toBeNull();
    expect(await screen.findByText("已连接")).toBeTruthy();
  });

  it("drops an old Runtime's QR confirmation after switching connection", async () => {
    setRemoteRuntimeConnection({ baseUrl: "https://a.example", token: "a" });
    let finish!: (value: ChannelAuthorizationSnapshot) => void;
    vi.mocked(runtimeClient.startChannelAuthorization).mockResolvedValue(
      authorization("pending"),
    );
    vi.mocked(runtimeClient.getChannelAuthorization).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<ChannelsSettingsSection context={context()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "扫码绑定微信" }),
    );
    await waitFor(() =>
      expect(runtimeClient.getChannelAuthorization).toHaveBeenCalled(),
    );
    const signal = vi.mocked(runtimeClient.getChannelAuthorization).mock
      .calls[0][2];
    act(() =>
      setRemoteRuntimeConnection({ baseUrl: "https://b.example", token: "b" }),
    );
    await screen.findByRole("button", { name: "扫码绑定微信" });
    await act(() =>
      Promise.resolve(finish(authorization("confirmed", connection()))),
    );
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText("我的微信")).toBeNull();
    expect(runtimeClient.cancelChannelAuthorization).not.toHaveBeenCalled();
  });
});

function context(characterId = "default"): DesktopSettingsContext {
  return {
    appearance: {
      character: { character_id: characterId },
    },
    runtime: {
      connection: "connected",
      health: null,
      error: null,
    },
  } as unknown as DesktopSettingsContext;
}

function authorization(
  status: ChannelAuthorizationSnapshot["status"],
  connected: ChannelConnectionSnapshot | null = null,
): ChannelAuthorizationSnapshot {
  return {
    auth_session_id: "00000000-0000-4000-8000-000000000201",
    provider_id: "weixin_ilink",
    status,
    expires_at: "2026-08-31T10:30:00+08:00",
    qr_code_content: ["pending", "scanned", "verification_required"].includes(
      status,
    )
      ? "weixin://pair/session-1"
      : null,
    verification_required: status === "verification_required",
    connection: connected,
    status_message: null,
    poll_after_ms: 1_000,
    created_at: "2026-08-31T10:00:00+08:00",
    updated_at: "2026-08-31T10:00:00+08:00",
  };
}

function connection(
  status: ChannelConnectionSnapshot["status"] = "ready",
  enabled = true,
  presentationPolicy?: ChannelConnectionSnapshot["configuration"]["presentation_policy"],
): ChannelConnectionSnapshot {
  return {
    configuration: {
      connection_id: "00000000-0000-4000-8000-000000000202",
      provider_id: "weixin_ilink",
      name: "我的微信",
      character_id: "default",
      principal_scope: "local",
      enabled,
      presentation_policy: presentationPolicy ?? {
        profile: "instant_message",
        cadence_enabled: true,
        stickers_enabled: false,
      },
    },
    capabilities: {
      chat_types: ["direct"],
      inbound_message_kinds: ["text", "image"],
      outbound_message_kinds: ["text", "image"],
      supports_typing: true,
    },
    revision: 1,
    status,
    last_seen_at: null,
    created_at: "2026-08-31T09:00:00+08:00",
    updated_at: "2026-08-31T10:00:00+08:00",
  };
}

function cancellableWait(
  signal?: AbortSignal,
): Promise<ChannelAuthorizationSnapshot> {
  return new Promise((_resolve, reject) => {
    signal?.addEventListener(
      "abort",
      () => reject(new DOMException("cancelled", "AbortError")),
      { once: true },
    );
  });
}
