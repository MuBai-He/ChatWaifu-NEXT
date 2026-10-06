import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as runtimeClient from "../chat/runtimeClient";
import type { ChannelConnectionSnapshot } from "../chat/runtimeClient";
import * as qqClient from "../chat/runtime-client/qqClient";
import type { QQPairingSnapshot } from "../chat/runtime-client/qqClient";
import { RuntimeRequestError } from "../chat/runtime-client/http";
import { QQChannelPanel } from "./QQChannelPanel";
import { setRemoteRuntimeConnection } from "../chat/runtimeEndpoint";

vi.mock("../chat/runtimeClient", () => ({
  getChannelConnections: vi.fn(),
  deleteChannelConnection: vi.fn(),
  updateChannelConnection: vi.fn(),
}));
vi.mock("../chat/runtime-client/qqClient", () => ({
  startQQPairing: vi.fn(),
  getQQPairing: vi.fn(),
  cancelQQPairing: vi.fn(),
  testQQChannelConnection: vi.fn(),
}));

describe("QQChannelPanel", () => {
  beforeEach(() => {
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([]);
    vi.mocked(runtimeClient.deleteChannelConnection).mockResolvedValue();
    vi.mocked(qqClient.startQQPairing).mockResolvedValue(pairing());
    vi.mocked(qqClient.cancelQQPairing).mockResolvedValue();
    vi.mocked(qqClient.getQQPairing).mockImplementation((_id, wait, signal) =>
      wait === 0
        ? Promise.resolve({
            ...pairing(),
            status: "cancelled",
            pairing_code: null,
          })
        : cancellableWait(signal),
    );
  });
  afterEach(() => {
    cleanup();
    setRemoteRuntimeConnection(null);
    vi.resetAllMocks();
  });

  it.each([
    { baseUrl: "https://runtime-b.example", token: "private-b" },
    { baseUrl: "https://runtime-a.example", token: "private-b" },
    {
      baseUrl: "https://runtime-a.example",
      token: "private-a",
      restartCount: 1,
    },
  ])(
    "discards Runtime A's late confirmation after context changes to %j",
    async (nextContext) => {
      setRemoteRuntimeConnection({
        baseUrl: "https://runtime-a.example",
        token: "private-a",
      });
      let finish!: (value: QQPairingSnapshot) => void;
      vi.mocked(qqClient.getQQPairing).mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      render(<QQChannelPanel characterId="default" runtimeOnline />);
      await openSetup();
      fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
      await screen.findByText("CW2 PAIR1234");
      await waitFor(() =>
        expect(qqClient.getQQPairing).toHaveBeenCalledTimes(1),
      );
      const b = {
        ...connection(),
        configuration: { ...connection().configuration, name: "Runtime B QQ" },
      };
      vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([b]);
      act(() => setRemoteRuntimeConnection(nextContext));
      await screen.findByText("Runtime B QQ");
      await act(() =>
        Promise.resolve(
          finish({
            ...pairing(),
            status: "confirmed",
            pairing_code: null,
            connection: {
              ...connection(),
              configuration: {
                ...connection().configuration,
                name: "Runtime A QQ",
              },
            },
          }),
        ),
      );
      expect(screen.getByText("Runtime B QQ")).toBeTruthy();
      expect(screen.queryByText("Runtime A QQ")).toBeNull();
      expect(screen.queryByText("CW2 PAIR1234")).toBeNull();
      expect(qqClient.getQQPairing).toHaveBeenCalledTimes(1);
      expect(vi.mocked(qqClient.getQQPairing).mock.calls[0]?.[2]?.aborted).toBe(
        true,
      );
    },
  );

  it("discards a late Runtime A pairing start without installing it in Runtime B", async () => {
    setRemoteRuntimeConnection({
      baseUrl: "https://runtime-a.example",
      token: "private-a",
    });
    let finish!: (value: QQPairingSnapshot) => void;
    vi.mocked(qqClient.startQQPairing).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    const b = {
      ...connection(),
      configuration: { ...connection().configuration, name: "Runtime B QQ" },
    };
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([b]);
    act(() =>
      setRemoteRuntimeConnection({
        baseUrl: "https://runtime-b.example",
        token: "private-b",
      }),
    );
    await screen.findByText("Runtime B QQ");
    await act(() =>
      Promise.resolve(
        finish({
          ...pairing(),
          status: "confirmed",
          pairing_code: null,
          connection: {
            ...connection(),
            configuration: {
              ...connection().configuration,
              name: "Runtime A QQ",
            },
          },
        }),
      ),
    );
    expect(screen.getByText("Runtime B QQ")).toBeTruthy();
    expect(screen.queryByText("Runtime A QQ")).toBeNull();
    expect(qqClient.getQQPairing).not.toHaveBeenCalled();
  });

  it("discards old connection health after an online Runtime switch", async () => {
    setRemoteRuntimeConnection({
      baseUrl: "https://runtime-a.example",
      token: "private-a",
    });
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      connection(),
    ]);
    let finish!: (value: ChannelConnectionSnapshot) => void;
    vi.mocked(qqClient.testQQChannelConnection).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    fireEvent.click(
      await screen.findByRole("button", { name: "检查 QQ 连接" }),
    );
    const b = {
      ...connection(),
      configuration: { ...connection().configuration, name: "Runtime B QQ" },
    };
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([b]);
    act(() =>
      setRemoteRuntimeConnection({
        baseUrl: "https://runtime-b.example",
        token: "private-b",
      }),
    );
    await screen.findByText("Runtime B QQ");
    await act(() =>
      Promise.resolve(
        finish({
          ...connection(),
          status: "error",
          configuration: {
            ...connection().configuration,
            name: "Runtime A QQ",
          },
        }),
      ),
    );
    expect(screen.getByText("Runtime B QQ")).toBeTruthy();
    expect(screen.queryByText("Runtime A QQ")).toBeNull();
    expect(screen.getByText("QQ 连接正常")).toBeTruthy();
  });

  it("does not follow an old cancellation by reading its pairing ID on Runtime B", async () => {
    setRemoteRuntimeConnection({
      baseUrl: "https://runtime-a.example",
      token: "private-a",
    });
    let finish!: () => void;
    vi.mocked(qqClient.cancelQQPairing).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "取消 QQ 配对" }),
    );
    const b = {
      ...connection(),
      configuration: { ...connection().configuration, name: "Runtime B QQ" },
    };
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([b]);
    act(() =>
      setRemoteRuntimeConnection({
        baseUrl: "https://runtime-b.example",
        token: "private-b",
      }),
    );
    await screen.findByText("Runtime B QQ");
    await act(() => Promise.resolve(finish()));
    expect(
      vi
        .mocked(qqClient.getQQPairing)
        .mock.calls.filter(([, wait]) => wait === 0),
    ).toHaveLength(0);
    expect(screen.getByText("Runtime B QQ")).toBeTruthy();
  });

  it.each(["toggle", "stickers", "disconnect"] as const)(
    "discards an old %s mutation result after a fresh Runtime B read",
    async (action) => {
      setRemoteRuntimeConnection({ baseUrl: "https://runtime-a.example" });
      const a: ChannelConnectionSnapshot = {
        ...connection(),
        capabilities: { outbound_message_kinds: ["text", "audio", "image"] },
      };
      vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([a]);
      let finishUpdate!: (value: ChannelConnectionSnapshot) => void;
      let finishDelete!: () => void;
      if (action === "disconnect") {
        vi.mocked(runtimeClient.deleteChannelConnection).mockReturnValueOnce(
          new Promise((resolve) => {
            finishDelete = resolve;
          }),
        );
      } else {
        vi.mocked(runtimeClient.updateChannelConnection).mockReturnValueOnce(
          new Promise((resolve) => {
            finishUpdate = resolve;
          }),
        );
      }
      render(<QQChannelPanel characterId="default" runtimeOnline />);
      await screen.findByText("QQ 连接正常");
      fireEvent.click(
        action === "disconnect"
          ? screen.getByRole("button", { name: "断开 QQ 连接" })
          : screen.getByRole("switch", {
              name:
                action === "toggle" ? "启用 QQ 私聊" : "允许角色发送表情图片",
            }),
      );
      const mutationSignal =
        action === "disconnect"
          ? vi.mocked(runtimeClient.deleteChannelConnection).mock.calls[0]?.[1]
              ?.signal
          : vi.mocked(runtimeClient.updateChannelConnection).mock.calls[0]?.[3];
      const b = {
        ...a,
        configuration: { ...a.configuration, name: "Runtime B QQ" },
      };
      vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([b]);
      act(() =>
        setRemoteRuntimeConnection({ baseUrl: "https://runtime-b.example" }),
      );
      await screen.findByText("Runtime B QQ");
      expect(mutationSignal?.aborted).toBe(true);
      await act(() => {
        if (action === "disconnect") finishDelete();
        else
          finishUpdate({
            ...a,
            configuration: { ...a.configuration, name: "Runtime A QQ" },
          });
        return Promise.resolve();
      });
      expect(screen.getByText("Runtime B QQ")).toBeTruthy();
      expect(screen.getByText("QQ 连接正常")).toBeTruthy();
      expect(screen.queryByText("Runtime A QQ")).toBeNull();
      expect(screen.queryByRole("button", { name: "设置 QQ 连接" })).toBeNull();
    },
  );

  it("discards an old Runtime A connection list after Runtime B is verified", async () => {
    setRemoteRuntimeConnection({ baseUrl: "https://runtime-a.example" });
    let finish!: (value: ChannelConnectionSnapshot[]) => void;
    vi.mocked(runtimeClient.getChannelConnections).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    await waitFor(() =>
      expect(runtimeClient.getChannelConnections).toHaveBeenCalledOnce(),
    );
    const b = {
      ...connection(),
      configuration: { ...connection().configuration, name: "Runtime B QQ" },
    };
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([b]);
    act(() =>
      setRemoteRuntimeConnection({ baseUrl: "https://runtime-b.example" }),
    );
    await screen.findByText("Runtime B QQ");
    await act(() =>
      Promise.resolve(
        finish([
          {
            ...connection(),
            configuration: {
              ...connection().configuration,
              name: "Runtime A QQ",
            },
          },
        ]),
      ),
    );
    expect(screen.getByText("Runtime B QQ")).toBeTruthy();
    expect(screen.queryByText("Runtime A QQ")).toBeNull();
  });

  it("persists sticker opt-in while keeping owner routing and voice request behavior", async () => {
    const original: ChannelConnectionSnapshot = {
      ...connection(),
      capabilities: {
        outbound_message_kinds: ["text", "audio", "image"],
      },
    };
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      original,
    ]);
    vi.mocked(runtimeClient.updateChannelConnection).mockImplementation(
      (_id, configuration) =>
        Promise.resolve({
          ...original,
          configuration,
          revision: 2,
        }),
    );
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    const toggle = await screen.findByRole<HTMLInputElement>("switch", {
      name: "允许角色发送表情图片",
    });
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle.checked).toBe(true));
    expect(runtimeClient.updateChannelConnection).toHaveBeenCalledWith(
      original.configuration.connection_id,
      {
        ...original.configuration,
        presentation_policy: {
          profile: "instant_message",
          stickers_enabled: true,
        },
      },
      original.revision,
      expect.any(AbortSignal),
      guardedRequestOptions(),
    );
    expect(screen.queryByRole("switch", { name: /语音/u })).toBeNull();
    view.rerender(
      <QQChannelPanel characterId="default" runtimeOnline={false} />,
    );
    expect(toggle.disabled).toBe(true);
    fireEvent.click(toggle);
    expect(runtimeClient.updateChannelConnection).toHaveBeenCalledTimes(1);
  });

  it("clears token when starting and shows the exact owner pairing command", async () => {
    let resolveStart!: (value: QQPairingSnapshot) => void;
    vi.mocked(qqClient.startQQPairing).mockReturnValue(
      new Promise((resolve) => {
        resolveStart = resolve;
      }),
    );
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    expect(
      screen.getByLabelText<HTMLInputElement>("NapCat 访问令牌").value,
    ).toBe("");
    expect(qqClient.startQQPairing).toHaveBeenCalledWith(
      "ws://127.0.0.1:3001",
      "test-token-123456789",
      "default",
      guardedRequestOptions(),
    );
    act(() => resolveStart(pairing()));
    expect(await screen.findByText("CW2 PAIR1234")).toBeTruthy();
    expect(screen.queryByLabelText("主人 QQ 号")).toBeNull();
    expect(screen.queryByRole("switch", { name: /语音/u })).toBeNull();
    view.unmount();
    await waitFor(() =>
      expect(qqClient.cancelQQPairing).toHaveBeenCalledWith(
        pairing().pairing_id,
        expect.objectContaining({
          expectedContext: expect.any(Object) as unknown,
        }),
      ),
    );
  });

  it("cancels a pairing that finishes starting after unmount", async () => {
    let resolveStart!: (value: QQPairingSnapshot) => void;
    vi.mocked(qqClient.startQQPairing).mockReturnValue(
      new Promise((resolve) => {
        resolveStart = resolve;
      }),
    );
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    view.unmount();
    act(() => resolveStart(pairing()));
    await waitFor(() =>
      expect(qqClient.cancelQQPairing).toHaveBeenCalledWith(
        pairing().pairing_id,
        expect.objectContaining({
          expectedContext: expect.any(Object) as unknown,
        }),
      ),
    );
  });

  it("aborts long polling offline and resumes without cancelling owner pairing", async () => {
    const signals: AbortSignal[] = [];
    vi.mocked(qqClient.getQQPairing).mockImplementation(
      (_id, _wait, signal) => {
        if (signal) signals.push(signal);
        return cancellableWait(signal);
      },
    );
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    await waitFor(() => expect(signals).toHaveLength(1));
    view.rerender(
      <QQChannelPanel characterId="default" runtimeOnline={false} />,
    );
    expect(signals[0]?.aborted).toBe(true);
    expect(qqClient.cancelQQPairing).not.toHaveBeenCalled();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "取消 QQ 配对" })
        .disabled,
    ).toBe(true);
    view.rerender(<QQChannelPanel characterId="default" runtimeOnline />);
    await waitFor(() => expect(signals).toHaveLength(2));
  });

  it("promotes confirmed pairing and never cancels the confirmed connection on unmount", async () => {
    vi.mocked(qqClient.getQQPairing).mockResolvedValue({
      ...pairing(),
      status: "confirmed",
      pairing_code: null,
      connection: connection(),
    });
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    expect(await screen.findByText("QQ 连接正常")).toBeTruthy();
    expect(screen.queryByText("CW2 PAIR1234")).toBeNull();
    view.unmount();
    expect(qqClient.cancelQQPairing).not.toHaveBeenCalled();
    expect(runtimeClient.deleteChannelConnection).not.toHaveBeenCalled();
  });

  it("explicitly cancels an active pairing", async () => {
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "取消 QQ 配对" }),
    );
    await waitFor(() => expect(screen.queryByText("CW2 PAIR1234")).toBeNull());
    expect(qqClient.cancelQQPairing).toHaveBeenCalledTimes(1);
  });

  it("retains disabled connections and supports health checks, enabling, and disconnecting", async () => {
    const disabled = {
      ...connection(),
      status: "disabled" as const,
      configuration: { ...connection().configuration, enabled: false },
    };
    vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
      disabled,
    ]);
    vi.mocked(qqClient.testQQChannelConnection).mockResolvedValue(disabled);
    vi.mocked(runtimeClient.updateChannelConnection).mockResolvedValue(
      connection(),
    );
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    expect(await screen.findByText("QQ 已停用")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "检查 QQ 连接" }));
    await waitFor(() =>
      expect(qqClient.testQQChannelConnection).toHaveBeenCalledWith(
        disabled.configuration.connection_id,
        expect.any(AbortSignal),
        guardedRequestOptions(),
      ),
    );
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "检查 QQ 连接" })
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("switch", { name: "启用 QQ 私聊" }));
    await waitFor(() =>
      expect(runtimeClient.updateChannelConnection).toHaveBeenCalledWith(
        disabled.configuration.connection_id,
        { ...disabled.configuration, enabled: true },
        disabled.revision,
        expect.any(AbortSignal),
        guardedRequestOptions(),
      ),
    );
    expect(await screen.findByText("QQ 连接正常")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "断开 QQ 连接" }));
    expect(
      await screen.findByRole("button", { name: "设置 QQ 连接" }),
    ).toBeTruthy();
    expect(runtimeClient.deleteChannelConnection).toHaveBeenCalledWith(
      disabled.configuration.connection_id,
      guardedRequestOptions(),
    );
  });

  it("refreshes health after Runtime reconnect and ignores a stale offline load", async () => {
    let resolveOld!: (value: ChannelConnectionSnapshot[]) => void;
    vi.mocked(runtimeClient.getChannelConnections)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
      )
      .mockResolvedValueOnce([{ ...connection(), status: "degraded" }]);
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    await waitFor(() =>
      expect(runtimeClient.getChannelConnections).toHaveBeenCalledTimes(1),
    );
    view.rerender(
      <QQChannelPanel characterId="default" runtimeOnline={false} />,
    );
    act(() => resolveOld([connection()]));
    view.rerender(<QQChannelPanel characterId="default" runtimeOnline />);
    expect(await screen.findByText("QQ 正在重连")).toBeTruthy();
    expect(screen.queryByText("QQ 连接正常")).toBeNull();
  });

  it.each([false, true])(
    "recovers a missing pairing after Runtime restart (durable connection: %s)",
    async (hasConnection) => {
      vi.mocked(runtimeClient.getChannelConnections)
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce(hasConnection ? [connection()] : []);
      vi.mocked(qqClient.getQQPairing)
        .mockImplementationOnce((_id, _wait, signal) => cancellableWait(signal))
        .mockRejectedValueOnce(
          new RuntimeRequestError("QQ pairing not found", 404),
        );
      const view = render(
        <QQChannelPanel characterId="default" runtimeOnline />,
      );
      await openSetup();
      fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
      await screen.findByText("CW2 PAIR1234");
      view.rerender(
        <QQChannelPanel characterId="default" runtimeOnline={false} />,
      );
      view.rerender(<QQChannelPanel characterId="default" runtimeOnline />);
      await waitFor(() =>
        expect(runtimeClient.getChannelConnections).toHaveBeenCalledTimes(3),
      );
      expect(screen.queryByText("CW2 PAIR1234")).toBeNull();
      if (hasConnection) {
        expect(await screen.findByText("QQ 连接正常")).toBeTruthy();
        expect(
          screen.queryByRole("button", { name: "开始 QQ 配对" }),
        ).toBeNull();
      } else {
        await screen.findByRole("button", { name: "开始 QQ 配对" });
        fireEvent.change(screen.getByLabelText("NapCat 访问令牌"), {
          target: { value: "test-token-123456789" },
        });
        expect(
          screen.getByRole<HTMLButtonElement>("button", {
            name: "开始 QQ 配对",
          }).disabled,
        ).toBe(false);
      }
      view.unmount();
      expect(qqClient.cancelQQPairing).not.toHaveBeenCalled();
    },
  );

  it("reconciles a confirmation that won the race against cancel", async () => {
    vi.mocked(runtimeClient.getChannelConnections)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([connection()]);
    vi.mocked(qqClient.getQQPairing).mockImplementation((_id, wait, signal) =>
      wait === 0
        ? Promise.resolve({
            ...pairing(),
            status: "confirmed",
            pairing_code: null,
            connection: connection(),
          })
        : cancellableWait(signal),
    );
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "取消 QQ 配对" }),
    );
    expect(await screen.findByText("QQ 连接正常")).toBeTruthy();
    expect(qqClient.getQQPairing).toHaveBeenCalledWith(
      pairing().pairing_id,
      0,
      expect.any(AbortSignal),
      guardedRequestOptions(),
    );
    expect(screen.queryByRole("button", { name: "开始 QQ 配对" })).toBeNull();
    view.unmount();
    expect(qqClient.cancelQQPairing).toHaveBeenCalledTimes(1);
    expect(runtimeClient.deleteChannelConnection).not.toHaveBeenCalled();
  });

  it("marks retained connection health unverified after failed reconnect refresh and permits explicit retry", async () => {
    vi.mocked(runtimeClient.getChannelConnections)
      .mockResolvedValueOnce([connection()])
      .mockRejectedValueOnce(new Error("连接读取失败"))
      .mockResolvedValueOnce([connection()]);
    const view = render(<QQChannelPanel characterId="default" runtimeOnline />);
    await screen.findByText("QQ 连接正常");
    view.rerender(
      <QQChannelPanel characterId="default" runtimeOnline={false} />,
    );
    view.rerender(<QQChannelPanel characterId="default" runtimeOnline />);
    expect(await screen.findByText("QQ 状态未确认")).toBeTruthy();
    expect(screen.queryByText("QQ 连接正常")).toBeNull();
    expect(
      view.container.querySelector(".channels-settings-state.connected"),
    ).toBeNull();
    expect(
      screen.getByRole<HTMLInputElement>("switch", { name: "启用 QQ 私聊" })
        .disabled,
    ).toBe(true);
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "检查 QQ 连接" })
        .disabled,
    ).toBe(true);
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "断开 QQ 连接" })
        .disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "刷新 QQ 连接状态" }));
    expect(await screen.findByText("QQ 连接正常")).toBeTruthy();
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "检查 QQ 连接" })
        .disabled,
    ).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not echo token-bearing server errors and rejects cleartext remote endpoints", async () => {
    vi.mocked(qqClient.startQQPairing).mockRejectedValue(
      new Error("secret test-token-123456789"),
    );
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    await openSetup();
    fireEvent.change(screen.getByLabelText("NapCat WebSocket 地址"), {
      target: { value: "ws://example.com:3001" },
    });
    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: "开始 QQ 配对" })
        .disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("NapCat WebSocket 地址"), {
      target: { value: "wss://example.com/onebot" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始 QQ 配对" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "无法开始 QQ 配对，请检查 NapCat 登录、连接地址和访问令牌后重试。",
    );
    expect(screen.queryByText(/test-token/u)).toBeNull();
  });
});

async function openSetup() {
  fireEvent.click(await screen.findByRole("button", { name: "设置 QQ 连接" }));
  fireEvent.change(screen.getByLabelText("NapCat 访问令牌"), {
    target: { value: "test-token-123456789" },
  });
}

function guardedRequestOptions(): unknown {
  return expect.objectContaining({
    expectedContext: expect.objectContaining({
      revision: expect.any(Number) as unknown,
      connection: expect.objectContaining({
        baseUrl: expect.any(String) as unknown,
      }) as unknown,
    }) as unknown,
    signal: expect.any(AbortSignal) as unknown,
  });
}

function pairing(): QQPairingSnapshot {
  return {
    schema_version: "1.0",
    pairing_id: "00000000-0000-4000-8000-000000000201",
    provider_id: "qq_napcat",
    status: "pending",
    pairing_code: "PAIR1234",
    account_label: "角色 QQ",
    expires_at: "2026-10-03T12:00:00+08:00",
    connection: null,
    error: null,
  };
}

function connection(): ChannelConnectionSnapshot {
  return {
    configuration: {
      connection_id: "00000000-0000-4000-8000-000000000202",
      provider_id: "qq_napcat",
      name: "角色 QQ",
      character_id: "default",
      principal_scope: "local",
      enabled: true,
    },
    revision: 1,
    status: "ready",
    last_seen_at: null,
    created_at: "2026-10-03T11:00:00+08:00",
    updated_at: "2026-10-03T11:00:00+08:00",
  };
}

function cancellableWait(signal?: AbortSignal): Promise<QQPairingSnapshot> {
  return new Promise((_resolve, reject) => {
    signal?.addEventListener(
      "abort",
      () => reject(new DOMException("cancelled", "AbortError")),
      { once: true },
    );
  });
}
