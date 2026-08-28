import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useSettingsOperation } from "./useSettingsOperation";

describe("useSettingsOperation", () => {
  it("normalizes pending, success, and busy state", async () => {
    let finish: ((value: string) => void) | undefined;
    const operation = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          finish = resolve;
        }),
    );
    const { result } = renderHook(() => useSettingsOperation<"save">());

    let promise: Promise<string | undefined> | undefined;
    act(() => {
      promise = result.current.run("save", operation, {
        pending: "保存中",
        success: (value) => `已保存 ${value}`,
        error: "保存失败",
      });
    });
    expect(result.current.busy).toBe("save");
    expect(result.current.notice).toEqual({ tone: "info", text: "保存中" });

    await act(async () => {
      finish?.("配置");
      await promise;
    });
    expect(result.current.busy).toBeNull();
    expect(result.current.notice).toEqual({
      tone: "success",
      text: "已保存 配置",
    });
  });

  it("normalizes thrown errors and ignores overlapping actions", async () => {
    let release: (() => void) | undefined;
    const { result } = renderHook(() =>
      useSettingsOperation<"save" | "test">(),
    );
    let first: Promise<void | undefined> | undefined;
    act(() => {
      first = result.current.run(
        "save",
        () =>
          new Promise<void>((resolve) => {
            release = resolve;
          }),
        { error: "保存失败" },
      );
    });

    let overlap: string | undefined;
    await act(async () => {
      overlap = await result.current.run(
        "test",
        () => Promise.resolve("unexpected"),
        { error: "测试失败" },
      );
    });
    expect(overlap).toBeUndefined();
    await act(async () => {
      release?.();
      await first;
    });

    await act(async () => {
      await result.current.run(
        "test",
        () => Promise.reject(new Error("服务拒绝连接")),
        { error: "测试失败" },
      );
    });
    expect(result.current.notice).toEqual({
      tone: "error",
      text: "服务拒绝连接",
    });
  });
});
