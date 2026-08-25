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
    page.getByRole("complementary", { name: "语音设置" }),
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
    page.getByRole("complementary", { name: "语音设置" }),
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

test("official bridge renders the locally supplied Live2D model", async ({
  page,
}, testInfo) => {
  test.skip(!realVendorReady, "local licensed Live2D vendor assets are absent");

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

  await page.getByRole("button", { name: "happy" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("happy");
  await page.getByRole("button", { name: "headpat" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("headpat");

  const screenshot = await page.getByTestId("avatar-canvas").screenshot({
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
