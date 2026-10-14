import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RuntimeConnectionGate } from "./RuntimeConnectionGate";
import { runtimeFetchWithConnection } from "../chat/runtimeEndpoint";
import { invoke } from "@tauri-apps/api/core";

vi.mock("../chat/runtimeEndpoint", () => ({
  isDesktopHost: () => true,
  runtimeFetchWithConnection: vi.fn(),
  setRemoteRuntimeConnection: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(() => Promise.resolve(vi.fn())),
}));

const token = "exchanged-session-token-12345678901234567890";
const password = " 有空格的密码 123456 ";
const respond = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(invoke).mockResolvedValue(null);
});
afterEach(cleanup);

async function enterCredentials() {
  render(
    <RuntimeConnectionGate>
      <p>已进入</p>
    </RuntimeConnectionGate>,
  );
  const field = await screen.findByPlaceholderText("输入登录密码");
  fireEvent.change(screen.getByPlaceholderText("http://192.168.1.103:18780"), {
    target: { value: "https://runtime.example" },
  });
  fireEvent.change(field, { target: { value: password } });
}

describe("Runtime password connection", () => {
  it("exchanges the exact password and persists only the random credential for native clients", async () => {
    vi.mocked(runtimeFetchWithConnection)
      .mockResolvedValueOnce(
        respond({ schema_version: 1, token, expires_in: 86400 }),
      )
      .mockResolvedValueOnce(respond({ items: [] }));
    await enterCredentials();
    fireEvent.click(screen.getByRole("button", { name: "连接并进入" }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("set_client_connection", {
        connection: {
          mode: "remote",
          base_url: "https://runtime.example",
          token,
        },
      }),
    );
    expect(runtimeFetchWithConnection).toHaveBeenNthCalledWith(
      1,
      { baseUrl: "https://runtime.example" },
      "/v1/runtime/password/login",
      expect.objectContaining({
        body: JSON.stringify({ password }),
        redirect: "error",
        cache: "no-store",
        credentials: "omit",
      }),
    );
    expect(runtimeFetchWithConnection).toHaveBeenNthCalledWith(
      2,
      { baseUrl: "https://runtime.example", token },
      "/v1/characters",
      expect.anything(),
    );
  });

  it("shows a wrong-password error without persisting a connection", async () => {
    vi.mocked(runtimeFetchWithConnection).mockResolvedValue(
      respond({ detail: "密码不正确。" }, 401),
    );
    await enterCredentials();
    fireEvent.click(screen.getByRole("button", { name: "连接并进入" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "密码不正确。",
    );
    expect(invoke).not.toHaveBeenCalledWith(
      "set_client_connection",
      expect.anything(),
    );
  });

  it("keeps legacy access-token connections available", async () => {
    vi.mocked(runtimeFetchWithConnection).mockResolvedValue(
      respond({ items: [] }),
    );
    await enterCredentials();
    fireEvent.change(screen.getByRole("combobox", { name: "登录方式" }), {
      target: { value: "token" },
    });
    fireEvent.change(screen.getByPlaceholderText("粘贴访问令牌"), {
      target: { value: token },
    });
    fireEvent.click(screen.getByRole("button", { name: "连接并进入" }));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith(
        "set_client_connection",
        expect.anything(),
      ),
    );
    expect(runtimeFetchWithConnection).toHaveBeenCalledTimes(1);
    expect(runtimeFetchWithConnection).toHaveBeenCalledWith(
      { baseUrl: "https://runtime.example", token },
      "/v1/characters",
      expect.anything(),
    );
  });

  it("asks for credentials again when a restored session has expired", async () => {
    vi.mocked(invoke).mockResolvedValue({
      mode: "remote",
      base_url: "https://runtime.example",
      token,
    });
    vi.mocked(runtimeFetchWithConnection).mockResolvedValue(respond({}, 401));
    render(
      <RuntimeConnectionGate>
        <p>已进入</p>
      </RuntimeConnectionGate>,
    );
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "登录已失效，请重新输入密码或访问令牌。",
    );
    expect(screen.getByPlaceholderText("输入登录密码")).toHaveProperty(
      "value",
      "",
    );
    expect(screen.queryByText("已进入")).toBeNull();
  });

  it("discards a late login result after unmount", async () => {
    let complete!: (response: Response) => void;
    vi.mocked(runtimeFetchWithConnection).mockReturnValue(
      new Promise((resolve) => {
        complete = resolve;
      }),
    );
    await enterCredentials();
    fireEvent.click(screen.getByRole("button", { name: "连接并进入" }));
    const signal = vi.mocked(runtimeFetchWithConnection).mock.calls[0][2]
      ?.signal;
    cleanup();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      await Promise.resolve(complete(respond({ schema_version: 1, token })));
    });
    expect(invoke).not.toHaveBeenCalledWith(
      "set_client_connection",
      expect.anything(),
    );
  });
});
