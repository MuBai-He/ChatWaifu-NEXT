import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  parseChannelOutboundIntentSnapshot,
  parseChannelProactivePolicySnapshot,
  parseChannelProactivePreview,
} from "@chatwaifu/protocol";
import * as client from "../chat/runtime-client/channelProactiveClient";
import * as runtimeClient from "../chat/runtimeClient";
import { RuntimeRequestError } from "../chat/runtime-client/http";
import { QQProactivePanel } from "./QQProactivePanel";
import { QQChannelPanel } from "./QQChannelPanel";

vi.mock("../chat/runtime-client/channelProactiveClient", () => ({
  getChannelProactivePolicy: vi.fn(),
  updateChannelProactivePolicy: vi.fn(),
  previewChannelProactivePolicy: vi.fn(),
  getChannelOutboundIntents: vi.fn(),
  cancelChannelOutboundIntent: vi.fn(),
}));
vi.mock("../chat/runtimeClient", () => ({
  getChannelConnections: vi.fn(),
  deleteChannelConnection: vi.fn(),
  updateChannelConnection: vi.fn(),
}));
const id = "00000000-0000-4000-8000-000000000001";
const otherId = "00000000-0000-4000-8000-000000000002";
const now = "2026-10-03T10:00:00+08:00";
const props = {
  connectionId: id,
  runtimeOnline: true,
  connectionVerified: true,
};

beforeEach(() => {
  vi.mocked(client.getChannelProactivePolicy).mockResolvedValue(snapshot());
  vi.mocked(client.getChannelOutboundIntents).mockResolvedValue({
    schema_version: "1.0",
    items: [],
    next_cursor: null,
  });
  vi.mocked(client.updateChannelProactivePolicy).mockImplementation(
    (_id, policy, revision) =>
      Promise.resolve({ ...snapshot(), policy, revision: revision + 1 }),
  );
  vi.mocked(client.previewChannelProactivePolicy).mockResolvedValue(
    parseChannelProactivePreview({
      connection_id: id,
      binding_id: id,
      policy_revision: 0,
      eligible: false,
      reason: "disabled",
      evaluated_at: now,
      reserved_today: 1,
      remaining_daily_budget: 2,
    }),
  );
  vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("QQ proactive operator panel", () => {
  it("reads server defaults with opt-in off and never writes during display", async () => {
    render(<QQProactivePanel {...props} />);
    const toggle = await readyToggle();
    expect(toggle.checked).toBe(false);
    expect(
      screen.getByLabelText<HTMLInputElement>("空闲等待（分钟）").value,
    ).toBe("45");
    expect(
      screen.getByLabelText<HTMLInputElement>("问候间隔（分钟）").value,
    ).toBe("60");
    expect(
      screen.getByLabelText<HTMLInputElement>("每日上限（次）").value,
    ).toBe("3");
    expect(
      screen.getByLabelText<HTMLInputElement>("问候有效期（分钟）").value,
    ).toBe("15");
    expect(screen.getByLabelText<HTMLInputElement>("QQ 问候时区").value).toBe(
      "Asia/Shanghai",
    );
    expect(
      screen.getByLabelText<HTMLInputElement>("QQ 安静时段开始").value,
    ).toBe("23:00");
    expect(
      screen.getByLabelText<HTMLInputElement>("QQ 安静时段结束").value,
    ).toBe("08:00");
    expect(screen.getByText(/固定目标/u)).toBeDefined();
    expect(screen.queryByLabelText(/收件人/u)).toBeNull();
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
    expect(client.previewChannelProactivePolicy).not.toHaveBeenCalled();
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "保存 QQ 问候设置",
      }).disabled,
    ).toBe(true);
  });
  it("requires explicit saving to enable and persists disabling with the new CAS", async () => {
    render(<QQProactivePanel {...props} />);
    const toggle = await readyToggle();
    fireEvent.click(toggle);
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "检查主动问候资格",
      }).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "保存 QQ 问候设置" }));
    await screen.findByText("QQ 主动文字设置已保存。");
    expect(client.updateChannelProactivePolicy).toHaveBeenCalledWith(
      id,
      expect.objectContaining({ enabled: true }),
      0,
      expect.any(AbortSignal),
    );
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "保存 QQ 问候设置" }));
    await screen.findByText(/已关闭 QQ 主动文字/u);
    expect(client.updateChannelProactivePolicy).toHaveBeenLastCalledWith(
      id,
      expect.objectContaining({ enabled: false }),
      1,
      expect.any(AbortSignal),
    );
    expect(toggle.checked).toBe(false);
  });
  it("checks saved eligibility without updating policy or creating a request", async () => {
    render(<QQProactivePanel {...props} />);
    await readyToggle();
    fireEvent.click(screen.getByRole("button", { name: "检查主动问候资格" }));
    await screen.findByText("主动文字已关闭");
    expect(screen.getByText(/今日已预留 1 次，剩余 2 次/u)).toBeDefined();
    expect(client.previewChannelProactivePolicy).toHaveBeenCalledWith(
      id,
      expect.any(AbortSignal),
    );
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
    expect(client.cancelChannelOutboundIntent).not.toHaveBeenCalled();
    expect(client.getChannelOutboundIntents).toHaveBeenCalledTimes(1);
  });
  it.each([
    ["问候有效期（分钟）", "61"],
    ["每日上限（次）", ""],
    ["空闲等待（分钟）", "1.5"],
    ["QQ 问候时区", "Invalid/Timezone"],
  ])("rejects invalid draft %s without a write", async (label, value) => {
    render(<QQProactivePanel {...props} />);
    await readyToggle();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "保存 QQ 问候设置",
      }).disabled,
    ).toBe(true);
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "检查主动问候资格",
      }).disabled,
    ).toBe(true);
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
  });
  it("cannot manage an unbound target even with a fetched policy", async () => {
    vi.mocked(client.getChannelProactivePolicy).mockResolvedValue({
      ...snapshot(),
      binding_id: null,
    });
    render(<QQProactivePanel {...props} />);
    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "允许角色主动发送 QQ 文字",
    });
    expect(toggle.disabled).toBe(true);
    fireEvent.click(toggle);
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: "检查主动问候资格",
      }).disabled,
    ).toBe(false);
  });
  it("keeps unbound history and eligibility readable without permitting mutations", async () => {
    vi.mocked(client.getChannelProactivePolicy).mockResolvedValue({
      ...snapshot(),
      binding_id: null,
    });
    const receipt = {
      ...intent(),
      delivery_status: "sending" as const,
      provider_receipt_present: true,
    };
    vi.mocked(client.getChannelOutboundIntents)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        items: [receipt],
        next_cursor: "older-receipts",
      })
      .mockResolvedValue({
        schema_version: "1.0",
        items: [
          {
            ...receipt,
            request_id: otherId,
            reply_text: "older confirmed text",
            cancelable: false,
          },
        ],
        next_cursor: null,
      });
    vi.mocked(client.previewChannelProactivePolicy).mockResolvedValue(
      parseChannelProactivePreview({
        connection_id: id,
        binding_id: null,
        policy_revision: 0,
        eligible: false,
        reason: "owner_binding_required",
        evaluated_at: now,
      }),
    );
    render(<QQProactivePanel {...props} />);
    await screen.findByText(/已记录服务端接受回执，无法撤回/u);
    const toggle = screen.getByRole<HTMLInputElement>("switch", {
      name: "允许角色主动发送 QQ 文字",
    });
    const save = screen.getByRole<HTMLButtonElement>("button", {
      name: "保存 QQ 问候设置",
    });
    const cancel = screen.getByRole<HTMLButtonElement>("button", {
      name: "停止未发送请求",
    });
    expect(toggle.disabled).toBe(true);
    expect(save.disabled).toBe(true);
    expect(cancel.disabled).toBe(true);
    fireEvent.click(toggle);
    fireEvent.click(save);
    fireEvent.click(cancel);
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
    expect(client.cancelChannelOutboundIntent).not.toHaveBeenCalled();
    const older = screen.getByRole<HTMLButtonElement>("button", {
      name: "查看更早请求",
    });
    expect(older.disabled).toBe(false);
    fireEvent.click(older);
    await screen.findByText("older confirmed text");
    expect(client.getChannelOutboundIntents).toHaveBeenLastCalledWith(
      id,
      "older-receipts",
      expect.any(AbortSignal),
    );
    expect(screen.getByText(/已记录服务端接受回执，无法撤回/u)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "检查主动问候资格" }));
    await screen.findByText("需要确认主人绑定");
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
    expect(client.cancelChannelOutboundIntent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "刷新主动文字状态" }));
    await waitFor(() =>
      expect(client.getChannelProactivePolicy).toHaveBeenCalledTimes(2),
    );
  });
  it("refreshes on policy conflict without retrying an old enable", async () => {
    vi.mocked(client.getChannelProactivePolicy)
      .mockResolvedValueOnce(snapshot())
      .mockResolvedValue({ ...snapshot(), revision: 2 });
    vi.mocked(client.updateChannelProactivePolicy).mockRejectedValue(
      new RuntimeRequestError("revision conflict", 409),
    );
    render(<QQProactivePanel {...props} />);
    fireEvent.click(await readyToggle());
    fireEvent.click(screen.getByRole("button", { name: "保存 QQ 问候设置" }));
    await waitFor(() =>
      expect(client.getChannelProactivePolicy).toHaveBeenCalledTimes(2),
    );
    const toggle = await readyToggle();
    expect(toggle.checked).toBe(false);
    expect(client.updateChannelProactivePolicy).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/版本已变化/u)).toBeDefined();
  });
  it("keeps old health unverified after failed reconnect refresh", async () => {
    vi.mocked(client.getChannelProactivePolicy)
      .mockResolvedValueOnce(snapshot())
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(snapshot());
    const view = render(<QQProactivePanel {...props} />);
    await readyToggle();
    view.rerender(<QQProactivePanel {...props} runtimeOnline={false} />);
    expect(
      screen.getByRole<HTMLInputElement>("switch", {
        name: "允许角色主动发送 QQ 文字",
      }).disabled,
    ).toBe(true);
    view.rerender(<QQProactivePanel {...props} />);
    await screen.findByText(/无法确认 QQ 主动文字设置/u);
    expect(
      screen.getByRole<HTMLInputElement>("switch", {
        name: "允许角色主动发送 QQ 文字",
      }).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "刷新主动文字状态" }));
    await readyToggle();
    expect(client.getChannelProactivePolicy).toHaveBeenCalledTimes(3);
  });
  it("aborts old reads and ignores a late enabled policy after context changes", async () => {
    const old = deferred<client.ChannelProactivePolicySnapshot>();
    let signal: AbortSignal | undefined;
    vi.mocked(client.getChannelProactivePolicy)
      .mockImplementationOnce((_id, readSignal) => {
        signal = readSignal;
        return old.promise;
      })
      .mockResolvedValue({ ...snapshot(), connection_id: otherId });
    const view = render(<QQProactivePanel {...props} />);
    await waitFor(() => expect(signal).toBeDefined());
    view.rerender(<QQProactivePanel {...props} connectionId={otherId} />);
    const toggle = await readyToggle();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      old.resolve({
        ...snapshot(),
        policy: { ...snapshot().policy, enabled: true },
      });
      await old.promise;
    });
    expect(toggle.checked).toBe(false);
    expect(client.updateChannelProactivePolicy).not.toHaveBeenCalled();
  });
  it("shows a late provider success after cancellation without claiming recall", async () => {
    const initial = intent();
    vi.mocked(client.getChannelOutboundIntents).mockResolvedValue({
      schema_version: "1.0",
      items: [initial],
      next_cursor: null,
    });
    vi.mocked(client.cancelChannelOutboundIntent).mockResolvedValue({
      ...initial,
      revision: 4,
      status: "settled",
      settled_reason: "cancelled",
      cancel_requested_at: now,
      delivery_status: "delivered",
      provider_receipt_present: true,
      cancelable: false,
    });
    render(<QQProactivePanel {...props} />);
    await readyToggle();
    fireEvent.click(
      await screen.findByRole("button", { name: "停止未发送请求" }),
    );
    await screen.findByText(/已记录服务端接受回执，无法撤回/u);
    expect(screen.getByText("已请求停止未发送部分。")).toBeDefined();
    expect(screen.queryByText(/撤回成功/u)).toBeNull();
    expect(screen.queryByRole("button", { name: "停止未发送请求" })).toBeNull();
    expect(client.cancelChannelOutboundIntent).toHaveBeenCalledWith(
      id,
      id,
      initial.revision,
      expect.any(AbortSignal),
    );
  });
  it("refreshes a cancellation conflict and displays the latest terminal result", async () => {
    const initial = intent();
    vi.mocked(client.getChannelOutboundIntents)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        items: [initial],
        next_cursor: null,
      })
      .mockResolvedValue({
        schema_version: "1.0",
        items: [
          {
            ...initial,
            status: "settled",
            delivery_status: "delivered",
            provider_receipt_present: true,
            cancelable: false,
          },
        ],
        next_cursor: null,
      });
    vi.mocked(client.cancelChannelOutboundIntent).mockRejectedValue(
      new RuntimeRequestError("conflict", 409),
    );
    render(<QQProactivePanel {...props} />);
    await readyToggle();
    fireEvent.click(
      await screen.findByRole("button", { name: "停止未发送请求" }),
    );
    await screen.findByText(/已记录服务端接受回执，无法撤回/u);
    expect(client.cancelChannelOutboundIntent).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "停止未发送请求" })).toBeNull();
  });
  it("replaces a bounded history page rather than accumulating records", async () => {
    vi.mocked(client.getChannelOutboundIntents)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        items: [intent()],
        next_cursor: "older-cursor",
      })
      .mockResolvedValue({
        schema_version: "1.0",
        items: [
          {
            ...intent(),
            request_id: otherId,
            reply_text: "older text",
            cancelable: false,
          },
        ],
        next_cursor: null,
      });
    render(<QQProactivePanel {...props} />);
    await readyToggle();
    fireEvent.click(screen.getByRole("button", { name: "查看更早请求" }));
    await screen.findByText("older text");
    expect(screen.queryByText("current text")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(client.getChannelOutboundIntents).toHaveBeenLastCalledWith(
      id,
      "older-cursor",
      expect.any(AbortSignal),
    );
  });
  it("does not fetch policies before a verified connection, and discards a late preview on context switch", async () => {
    const result = deferred<client.ChannelProactivePreview>();
    let previewSignal: AbortSignal | undefined;
    vi.mocked(client.previewChannelProactivePolicy).mockImplementation(
      (_id, signal) => {
        previewSignal = signal;
        return result.promise;
      },
    );
    const view = render(
      <QQProactivePanel {...props} connectionVerified={false} />,
    );
    expect(client.getChannelProactivePolicy).not.toHaveBeenCalled();
    view.rerender(<QQProactivePanel {...props} />);
    await readyToggle();
    fireEvent.click(screen.getByRole("button", { name: "检查主动问候资格" }));
    await waitFor(() => expect(previewSignal).toBeDefined());
    vi.mocked(client.getChannelProactivePolicy).mockResolvedValue({
      ...snapshot(),
      connection_id: otherId,
    });
    view.rerender(<QQProactivePanel {...props} connectionId={otherId} />);
    await readyToggle();
    await act(async () => {
      result.resolve(
        parseChannelProactivePreview({
          connection_id: id,
          binding_id: id,
          policy_revision: 0,
          eligible: true,
          reason: "eligible",
          evaluated_at: now,
        }),
      );
      await result.promise;
    });
    expect(previewSignal?.aborted).toBe(true);
    expect(screen.queryByText("当前符合问候资格，检查不会触发发送")).toBeNull();
  });
  it("does not expose proactive management before the provider declares support", async () => {
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      {
        configuration: {
          connection_id: id,
          provider_id: "qq_napcat",
          name: "角色 QQ",
          character_id: "default",
          principal_scope: "owner/local",
        },
        revision: 1,
        status: "ready",
        created_at: now,
        updated_at: now,
        capabilities: { supports_proactive_messages: false },
      },
    ]);
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    await screen.findByText("角色 QQ");
    expect(screen.queryByRole("region", { name: "QQ 主动文字" })).toBeNull();
    expect(client.getChannelProactivePolicy).not.toHaveBeenCalled();
  });
  it("mounts only after QQ advertises complete proactive capability", async () => {
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      {
        configuration: {
          connection_id: id,
          provider_id: "qq_napcat",
          name: "角色 QQ",
          character_id: "default",
          principal_scope: "owner/local",
          enabled: true,
        },
        revision: 1,
        status: "ready",
        created_at: now,
        updated_at: now,
        capabilities: { supports_proactive_messages: true },
      },
    ]);
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    await readyToggle();
    expect(client.getChannelProactivePolicy).toHaveBeenCalledWith(
      id,
      expect.any(AbortSignal),
    );
    expect(screen.getByText(/角色可按本轮语义选择调用语音工具/u)).toBeDefined();
  });
});
function snapshot() {
  return parseChannelProactivePolicySnapshot({
    connection_id: id,
    binding_id: id,
  });
}
function intent() {
  return parseChannelOutboundIntentSnapshot({
    request_id: id,
    connection_id: id,
    binding_id: id,
    session_id: id,
    turn_id: id,
    generation_id: id,
    status: "planned",
    policy_revision: 0,
    route_revision: 1,
    revision: 3,
    not_before_at: now,
    expires_at: now,
    created_at: now,
    updated_at: now,
    reply_text: "current text",
    cancelable: true,
  });
}
async function readyToggle() {
  const toggle = await screen.findByRole<HTMLInputElement>("switch", {
    name: "允许角色主动发送 QQ 文字",
  });
  await waitFor(() => expect(toggle.disabled).toBe(false));
  return toggle;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
