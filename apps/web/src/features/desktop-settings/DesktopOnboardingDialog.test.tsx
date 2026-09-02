import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopOnboardingDialog } from "./DesktopOnboardingDialog";

describe("desktop onboarding dialog", () => {
  afterEach(cleanup);

  it("guides through install boundaries, API, TTS, STT, and completion", () => {
    const complete = vi.fn();
    render(
      <DesktopOnboardingDialog
        open
        onDefer={vi.fn()}
        onComplete={complete}
        onNavigate={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "欢迎来到 ChatWaifu NEXT" }),
    ).toBeTruthy();
    expect(screen.getByText(/不包含 CUDA、PyTorch 或大型模型/)).toBeTruthy();
    expect(screen.getByText(/现在可跳过，以后在“数据”页随时安装/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(
      screen.getByRole("heading", { name: "先让宁宁会聊天" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开模型设置" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByRole("heading", { name: "选择角色声音" })).toBeTruthy();
    expect(
      screen.getByText(/Qwen3-TTS 只有在对应 .cwpack 已安装/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(
      screen.getByRole("heading", { name: "连接麦克风与语音识别" }),
    ).toBeTruthy();
    expect(screen.getByText(/faster-whisper .cwpack/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "完成引导" }));
    expect(complete).toHaveBeenCalledOnce();
  });

  it("opens a concrete settings section and can defer with Escape", () => {
    const navigate = vi.fn();
    const defer = vi.fn();
    render(
      <DesktopOnboardingDialog
        open
        onDefer={defer}
        onComplete={vi.fn()}
        onNavigate={navigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "打开模型设置" }));
    expect(navigate).toHaveBeenCalledWith("models");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(defer).toHaveBeenCalledOnce();
  });
});
