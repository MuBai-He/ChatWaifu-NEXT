import { LIVE2D_LAB_MANIFEST } from "@chatwaifu/avatar-sdk";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { AppearanceSettingsSection } from "./AppearanceSettingsSection";

describe("AppearanceSettingsSection", () => {
  it("shows the local Live2D model identity and honest attribution", () => {
    render(<AppearanceSettingsSection context={context()} />);

    const attribution = screen.getByRole("group", {
      name: "Live2D 模型与署名",
    });
    expect(attribution.textContent).toContain("ayachi-nene-local");
    expect(attribution.textContent).toContain("模型作者");
    expect(attribution.textContent).toContain("涂抹一画");
    expect(attribution.textContent).toContain("YUZUSOFT/JUNOS");
    expect(attribution.textContent).toContain("仅限私人研究与本机验证");
    expect(
      screen
        .getByRole("link", { name: /拥有全服装的Live2D宁宁/ })
        .getAttribute("href"),
    ).toBe("https://www.bilibili.com/video/BV1MLgYzmEz9");
  });
});

function context(): DesktopSettingsContext {
  return {
    canvasRef: { current: null },
    appearance: {
      avatarManifest: LIVE2D_LAB_MANIFEST,
      snapshot: { status: "ready" },
      rendererKind: "live2d",
      character: { display_name: "绫地宁宁" },
    },
    desktop: {
      loading: false,
      saving: false,
      desktopHost: true,
      preferences: {
        overlayVisible: true,
        alwaysOnTop: true,
        clickThrough: true,
        showSubtitles: true,
      },
      setOverlayVisible: () => undefined,
      setAlwaysOnTop: () => undefined,
      setClickThrough: () => undefined,
      setDisplay: () => undefined,
    },
  } as unknown as DesktopSettingsContext;
}
