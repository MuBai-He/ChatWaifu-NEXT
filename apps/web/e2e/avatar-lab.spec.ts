import { existsSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const realVendorReady = [
  "public/vendor/live2d/live2dcubismcore.min.js",
  "public/vendor/live2d/chatwaifu-live2d-bridge.js",
  "public/vendor/live2d/model/avatar.model3.json",
].every((relativePath) => existsSync(path.resolve(relativePath)));

interface TranscriptMetrics {
  transcriptClientHeight: number;
  transcriptScrollHeight: number;
  transcriptOverflow?: string;
}

test("Avatar Lab runs independently with semantic cues, hit testing, and screenshots", async ({
  page,
}, testInfo) => {
  await page.goto("/avatar-lab");
  await expect(
    page.getByRole("heading", { name: "Live2D Avatar Lab" }),
  ).toBeVisible();
  await expect(page.getByTestId("renderer-status")).toContainText("ready");

  await page.getByRole("button", { name: "listening" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("listening");
  await page.getByRole("button", { name: "thinking" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("thinking");
  await page.getByRole("button", { name: "pointer" }).click();
  await expect(page.locator(".timeline-layers")).toContainText("pointer");
  await page.getByRole("button", { name: "happy" }).click();
  await page.getByRole("button", { name: "headpat" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("happy");
  await page.getByRole("button", { name: "stare" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("stare");

  const canvas = page.getByTestId("avatar-canvas");
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("avatar canvas has no bounds");
  await page.mouse.click(
    bounds.x + bounds.width / 2,
    bounds.y + bounds.height * 0.28,
  );
  await expect(page.getByTestId("last-interaction")).toContainText(
    "touched_head",
  );

  await page.getByRole("button", { name: "Sine envelope" }).click();
  await page.getByRole("button", { name: "speaking" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("speaking");

  await page.getByRole("button", { name: "interrupt" }).click();
  await expect(page.locator(".timeline-layers")).toContainText("interrupt");
  await page.getByRole("button", { name: "reset" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("idle");
  await expect(page.getByTestId("semantic-state")).toContainText("neutral");
  await expect(page.locator(".timeline-layers")).toContainText(
    "No active cues",
  );

  const screenshot = await page.screenshot({
    animations: "disabled",
    fullPage: true,
    path: testInfo.outputPath("avatar-lab.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(10_000);
});

test("missing proprietary Cubism Core produces an actionable error", async ({
  page,
}) => {
  await page.route("**/vendor/live2d/live2dcubismcore.min.js", (route) =>
    route.abort(),
  );
  await page.goto("/avatar-lab");
  await page.getByLabel("Renderer").selectOption("live2d");

  await expect(page.getByTestId("renderer-error")).toContainText(
    "avatar.live2d_core_missing",
  );
  await expect(page.getByTestId("renderer-error")).toContainText(
    "vendor/live2d/README.md",
  );
});

test("desktop chat keeps the visual-novel stage fixed while backlog scrolls", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await expect(page.locator(".vn-stage")).toBeVisible();
  await expect(page.getByRole("region", { name: "当前对话" })).toBeVisible();
  await page.getByRole("button", { name: /LOG.*历史/ }).click();
  await expect(
    page.getByRole("complementary", { name: "对话历史" }),
  ).toBeVisible();
  await page.evaluate(`
    (() => {
      const transcript = document.querySelector(".vn-history-list");
      if (!transcript) throw new Error("history list is missing");
      for (let index = 0; index < 40; index += 1) {
        const message = document.createElement("article");
        message.className = index % 2
          ? "vn-history-message user"
          : "vn-history-message assistant";
        const speaker = document.createElement("span");
        speaker.textContent = index % 2 ? "你" : "绫地宁宁";
        const text = document.createElement("p");
        text.textContent = "用于滚动边界验收的第 " + (index + 1) + " 条消息。";
        message.append(speaker, text);
        transcript.append(message);
      }
    })()
  `);

  const transcriptMetrics = await page.evaluate<TranscriptMetrics>(`
    (() => {
      const transcript = document.querySelector(".vn-history-list");
      if (!transcript) throw new Error("history list is missing");
      return {
        transcriptClientHeight: transcript.clientHeight,
        transcriptScrollHeight: transcript.scrollHeight,
        transcriptOverflow: getComputedStyle(transcript).overflowY,
      };
    })()
  `);
  const pageHeight = await page.evaluate<number>(
    "document.documentElement.scrollHeight",
  );
  const avatar = await page.locator(".avatar-frame").boundingBox();
  const stage = await page.locator(".vn-stage").boundingBox();
  const dialogue = await page.locator(".vn-dialogue").boundingBox();
  const transcript = await page.locator(".vn-history-list").boundingBox();
  const microphoneButton = await page.locator(".vn-voice-button").boundingBox();
  if (!avatar || !stage || !dialogue || !transcript || !microphoneButton) {
    throw new Error("chat layout is incomplete");
  }
  const viewportHeight = page.viewportSize()?.height ?? 0;
  const microphoneHitTarget = await page.evaluate<boolean>(`
    (() => {
      const button = document.querySelector(".vn-voice-button");
      if (!button) return false;
      const bounds = button.getBoundingClientRect();
      return document
        .elementFromPoint(
          bounds.x + bounds.width / 2,
          bounds.y + bounds.height / 2,
        )
        ?.closest("button") === button;
    })()
  `);

  expect(pageHeight).toBeLessThanOrEqual(viewportHeight + 1);
  expect(transcriptMetrics.transcriptScrollHeight).toBeGreaterThan(
    transcriptMetrics.transcriptClientHeight,
  );
  expect(transcriptMetrics.transcriptOverflow).toBe("auto");
  expect(microphoneHitTarget).toBe(true);
  expect(avatar.y + avatar.height).toBeLessThanOrEqual(viewportHeight);
  expect(stage.y + stage.height).toBeLessThanOrEqual(viewportHeight);
  expect(dialogue.y + dialogue.height).toBeLessThanOrEqual(viewportHeight);

  await page.getByRole("button", { name: "关闭对话历史" }).click();
  await page.getByRole("button", { name: /CONFIG.*设置/ }).click();
  await expect(
    page.getByRole("complementary", { name: "角色和模型设置" }),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "角色构图" })).toHaveValue(
    "bust",
  );
  await page.getByRole("combobox", { name: "角色构图" }).selectOption("full");
  await expect(page.locator(".vn-avatar")).toHaveClass(/framing-full/);
  await expect(
    page.getByRole("combobox", { name: "语音响应方式" }),
  ).toBeVisible();
});

test("visual-novel controls remain reachable on a narrow viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await expect(page.locator(".vn-stage")).toBeVisible();
  await expect(page.getByRole("region", { name: "当前对话" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  await page.getByRole("button", { name: /CONFIG.*设置/ }).click();
  await expect(
    page.getByRole("complementary", { name: "角色和模型设置" }),
  ).toBeVisible();

  await expect
    .poll(() =>
      page.evaluate<number>(`
        (() => {
          const panel = document.querySelector(".vn-settings-panel");
          if (!panel) return Number.POSITIVE_INFINITY;
          const bounds = panel.getBoundingClientRect();
          return Math.max(
            0,
            -bounds.left,
            bounds.right - window.innerWidth,
            -bounds.top,
            bounds.bottom - window.innerHeight,
          );
        })()
      `),
    )
    .toBeLessThanOrEqual(1);
  expect(
    await page.evaluate<number>("document.documentElement.scrollHeight"),
  ).toBeLessThanOrEqual(844);
});

test("desktop settings is an app-like control surface without chat ownership", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 960, height: 700 });
  await page.goto("/desktop-settings");

  await expect(
    page.getByRole("heading", { level: 1, name: "桌宠" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "设置分类" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Conversation" })).toHaveCount(
    0,
  );

  const subtitles = page.getByRole("switch", { name: "显示字幕" });
  await expect(subtitles).toBeEnabled();
  await subtitles.click();
  await expect(subtitles).not.toBeChecked();

  await page.getByRole("button", { name: /声音.*角色语音/ }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "声音" }),
  ).toBeVisible();
  await expect(page.getByText(/设置页不会建立第二条媒体链路/)).toBeVisible();

  const metrics = await page.evaluate<{
    pageHeight: number;
    viewportHeight: number;
    overflowY: string;
  }>(`
    (() => {
      const scroll = document.querySelector(".desktop-settings-scroll");
      if (!scroll) throw new Error("desktop settings scroll container is missing");
      return {
        pageHeight: document.documentElement.scrollHeight,
        viewportHeight: window.innerHeight,
        overflowY: getComputedStyle(scroll).overflowY,
      };
    })()
  `);
  expect(metrics.pageHeight).toBeLessThanOrEqual(metrics.viewportHeight + 1);
  expect(metrics.overflowY).toBe("auto");

  const screenshot = await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("desktop-settings.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(10_000);
});

test("desktop pet reveals its controls and composer while the pointer is over the pet", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 430, height: 650 });
  await page.goto("/desktop-pet");

  const actions = page.getByRole("navigation", { name: "桌宠操作" });
  const composer = page.getByRole("textbox", { name: "桌宠文字消息" });
  await expect(actions).toHaveCSS("opacity", "0");
  await expect(actions).toHaveCSS("pointer-events", "none");
  await expect(composer.locator("..")).toHaveCSS("opacity", "0");
  await expect(composer.locator("..")).toHaveCSS("pointer-events", "none");

  await page.getByRole("button", { name: "摸摸绫地宁宁" }).hover({
    position: { x: 200, y: 80 },
  });

  await expect(actions).toHaveCSS("opacity", "1");
  await expect(actions).toHaveCSS("pointer-events", "auto");
  await expect(composer.locator("..")).toHaveCSS("opacity", "1");
  await expect(composer.locator("..")).toHaveCSS("pointer-events", "auto");

  const dialogueOverflow = await page.evaluate<{
    overflowY: string;
    lineClamp: string;
    scrollBehavior: string;
  }>(`
    (() => {
      const container = document.createElement("section");
      container.className = "desktop-pet-dialogue";
      const dialogue = document.createElement("p");
      container.append(dialogue);
      document.body.append(container);
      try {
        const style = getComputedStyle(dialogue);
        return {
          overflowY: style.overflowY,
          lineClamp: style.webkitLineClamp,
          scrollBehavior: style.scrollBehavior,
        };
      } finally {
        container.remove();
      }
    })()
  `);
  expect(dialogueOverflow.overflowY).toBe("auto");
  expect(dialogueOverflow.lineClamp).toBe("none");
  expect(dialogueOverflow.scrollBehavior).toBe("auto");

  const screenshot = await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("desktop-pet-hover-composer.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(10_000);
});

test("official bridge renders the locally supplied Live2D model", async ({
  page,
}, testInfo) => {
  test.skip(!realVendorReady, "local licensed Live2D vendor assets are absent");

  await page.setViewportSize({ width: 1280, height: 1200 });
  await page.goto("/avatar-lab");
  await page.getByLabel("Renderer").selectOption("live2d");
  await expect(page.getByTestId("renderer-status")).toContainText("ready", {
    timeout: 25_000,
  });
  await expect(
    page
      .locator(".telemetry-grid > div")
      .filter({ hasText: "Resources" })
      .locator("strong"),
  ).not.toHaveText("0");

  const canvas = page.getByTestId("avatar-canvas");
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("Live2D canvas has no bounds");
  const interaction = page.getByTestId("last-interaction");
  await page.mouse.click(
    bounds.x + bounds.width * 0.1,
    bounds.y + bounds.height * 0.1,
  );
  await expect(interaction).toHaveText("touch the avatar");
  await page.mouse.click(
    bounds.x + bounds.width * 0.5,
    bounds.y + bounds.height * 0.2,
  );
  await expect(interaction).toContainText("touched_avatar");
  await page.mouse.click(
    bounds.x + bounds.width * 0.5,
    bounds.y + bounds.height * 0.35,
  );
  await expect(interaction).toContainText("touched_body");
  await page.mouse.click(
    bounds.x + bounds.width * 0.52,
    bounds.y + bounds.height * 0.76,
  );
  await expect(interaction).toContainText("touched_avatar");

  await page.getByRole("button", { name: "happy" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("happy");
  await page.getByRole("button", { name: "headpat" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("headpat");

  const screenshot = await canvas.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("live2d-local-model.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(20_000);
});

test("main chat uses the installed Live2D renderer", async ({
  page,
}, testInfo) => {
  test.skip(!realVendorReady, "local licensed Live2D vendor assets are absent");

  await page.goto("/");
  const avatar = page.getByRole("button", { name: "Touch avatar" });
  await expect(avatar).toHaveAttribute("data-avatar-status", "ready", {
    timeout: 25_000,
  });
  await expect(page.locator(".avatar-state")).toContainText("Live2D · idle");
  await avatar.click();
  await expect(page.locator(".avatar-state")).toContainText("happy");
  await expect(page.locator(".avatar-state")).toContainText("headpat");

  const screenshot = await page.locator(".avatar-frame canvas").screenshot({
    animations: "disabled",
    path: testInfo.outputPath("chat-live2d-nene.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(10_000);
});
