import { expect, test } from "@playwright/test";

test.skip(
  process.env.CHATWAIFU_E2E_RUNTIME !== "1",
  "requires the deterministic Runtime service",
);

test("main chat completes a real Runtime-backed turn", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator(".vn-runtime.connected")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("alert")).toHaveCount(0);

  const message = page.getByRole("textbox", { name: "Message" });
  await expect(message).toBeEnabled();
  await message.fill("这是 CI 的真实 Runtime 集成回合。");
  await page.getByRole("button", { name: "Send message" }).click();

  await expect(page.locator(".typing-caret")).toBeVisible();
  await expect(page.locator(".typing-caret")).toHaveCount(0, {
    timeout: 20_000,
  });
  await page.getByRole("button", { name: /LOG.*历史/ }).click();
  await expect(page.locator(".vn-history-message.user")).toContainText(
    "这是 CI 的真实 Runtime 集成回合。",
  );
  await expect(page.locator(".vn-history-message.assistant")).toHaveCount(1);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
