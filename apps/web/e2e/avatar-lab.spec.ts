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
  await page.getByRole("button", { name: "nod" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("happy");
  await page.getByRole("button", { name: "wave" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("wave");

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

test("desktop chat keeps the avatar visible while only the transcript scrolls", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await expect(page.locator(".conversation-panel")).toBeVisible();
  await page.evaluate(`
    (() => {
      const transcript = document.querySelector(".transcript");
      if (!transcript) throw new Error("transcript is missing");
      for (let index = 0; index < 40; index += 1) {
        const message = document.createElement("article");
        message.className = index % 2 ? "message user" : "message assistant";
        const avatar = document.createElement("div");
        avatar.className = "message-avatar";
        avatar.textContent = index % 2 ? "你" : "雾";
        const body = document.createElement("div");
        const text = document.createElement("p");
        text.textContent = "用于滚动边界验收的第 " + (index + 1) + " 条消息。";
        body.append(text);
        message.append(avatar, body);
        transcript.append(message);
      }
    })()
  `);

  const transcriptMetrics = await page.evaluate<TranscriptMetrics>(`
    (() => {
      const transcript = document.querySelector(".transcript");
      if (!transcript) throw new Error("transcript is missing");
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
  const grid = await page.locator(".demo-grid").boundingBox();
  const voiceBar = await page.locator(".voice-bar").boundingBox();
  const transcript = await page.locator(".transcript").boundingBox();
  const microphoneButton = await page
    .locator(".microphone-button")
    .boundingBox();
  if (!avatar || !grid || !voiceBar || !transcript || !microphoneButton) {
    throw new Error("chat layout is incomplete");
  }
  const viewportHeight = page.viewportSize()?.height ?? 0;
  const microphoneHitTarget = await page.evaluate<boolean>(`
    (() => {
      const button = document.querySelector(".microphone-button");
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
  expect(voiceBar.y + voiceBar.height).toBeLessThanOrEqual(transcript.y + 1);
  expect(microphoneHitTarget).toBe(true);
  expect(avatar.y + avatar.height).toBeLessThanOrEqual(viewportHeight);
  expect(grid.y + grid.height).toBeLessThanOrEqual(viewportHeight);
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
  await page.getByRole("button", { name: "nod" }).click();
  await expect(page.getByTestId("semantic-state")).toContainText("happy");

  const screenshot = await page.getByTestId("avatar-canvas").screenshot({
    animations: "disabled",
    path: testInfo.outputPath("live2d-natori.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(20_000);
});

test("main chat uses the installed Live2D renderer", async ({
  page,
}, testInfo) => {
  test.skip(!realVendorReady, "local licensed Live2D vendor assets are absent");

  await page.goto("/");
  await expect(page.locator(".avatar-state")).toContainText("Live2D · idle", {
    timeout: 25_000,
  });
  await page.getByRole("button", { name: "Touch avatar" }).click();
  await expect(page.locator(".avatar-state")).toContainText("happy");

  const screenshot = await page.locator(".avatar-frame canvas").screenshot({
    animations: "disabled",
    path: testInfo.outputPath("chat-live2d-natori.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(10_000);
});
