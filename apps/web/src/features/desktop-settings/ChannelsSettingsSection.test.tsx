import {
  cleanup,
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
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { ChannelsSettingsSection } from "./ChannelsSettingsSection";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value }: { value: string }) => (
    <svg data-testid="weixin-qr" data-value={value} />
  ),
}));

vi.mock("../chat/runtimeClient", () => ({
  cancelChannelAuthorization: vi.fn(),
  deleteChannelConnection: vi.fn(),
  getChannelAuthorization: vi.fn(),
  getChannelConnections: vi.fn(),
  startChannelAuthorization: vi.fn(),
  submitChannelAuthorizationVerification: vi.fn(),
}));

describe("ChannelsSettingsSection", () => {
  beforeEach(() => {
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([]);
    vi.mocked(runtimeClient.cancelChannelAuthorization).mockResolvedValue();
    vi.mocked(runtimeClient.deleteChannelConnection).mockResolvedValue();
    vi.mocked(
      runtimeClient.submitChannelAuthorizationVerification,
    ).mockResolvedValue(authorization("scanned"));
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
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
      ),
    );
    expect(
      (await screen.findByTestId("weixin-qr")).getAttribute("data-value"),
    ).toBe("weixin://pair/session-1");
    expect(screen.queryByLabelText("手机验证码")).toBeNull();
    expect(view.container.querySelectorAll("input")).toHaveLength(0);

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
      ).toHaveBeenCalledWith("00000000-0000-4000-8000-000000000201", "123456"),
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
    fireEvent.click(await screen.findByRole("button", { name: "取消绑定" }));
    await waitFor(() =>
      expect(runtimeClient.cancelChannelAuthorization).toHaveBeenCalledWith(
        "00000000-0000-4000-8000-000000000201",
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
    await waitFor(() =>
      expect(runtimeClient.deleteChannelConnection).toHaveBeenCalledWith(
        "00000000-0000-4000-8000-000000000202",
      ),
    );
  });
});

function context(): DesktopSettingsContext {
  return {
    appearance: {
      character: { character_id: "default" },
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
): ChannelConnectionSnapshot {
  return {
    configuration: {
      connection_id: "00000000-0000-4000-8000-000000000202",
      provider_id: "weixin_ilink",
      name: "我的微信",
      character_id: "default",
      principal_scope: "local",
      enabled,
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
