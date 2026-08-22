import { expect, test } from "@playwright/test";

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
  await page.goto("/avatar-lab");
  await page.getByLabel("Renderer").selectOption("live2d");

  await expect(page.getByTestId("renderer-error")).toContainText(
    "avatar.live2d_core_missing",
  );
  await expect(page.getByTestId("renderer-error")).toContainText(
    "vendor/live2d/README.md",
  );
});
