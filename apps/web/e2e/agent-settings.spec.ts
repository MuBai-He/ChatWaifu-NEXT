import { expect, test } from "@playwright/test";

test.skip(
  process.env.CHATWAIFU_E2E_AGENT !== "1",
  "Requires disposable Agent fixture",
);

test("real Agent API discovery, task grants, cancellation, immutable download and preview", async ({
  page,
}, info) => {
  const desktop = info.project.name === "agent-desktop";
  await page.goto(desktop ? "/desktop-settings" : "/settings/agent");
  if (desktop) await page.getByRole("button", { name: /能力与任务/ }).click();
  await expect(
    page.getByRole("heading", { name: "宁宁的能力与任务" }),
  ).toBeVisible();
  await expect(
    page.getByText("documents.create / word", { exact: true }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/未配置|需要授权/).first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: /显示更多能力/ }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "查找能力", exact: true })
    .fill("Word");
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("button", { name: /显示更多能力/ })).toHaveCount(
    0,
  );
  await page.getByText("创建授权任务", { exact: true }).click();
  const target = `浏览器验收 ${info.project.name} ${Date.now()}`;
  await page.getByRole("textbox", { name: "目标", exact: true }).fill(target);
  await page
    .getByRole("checkbox", { name: "workspace.files", exact: true })
    .check();
  await page
    .getByRole("checkbox", { name: /允许任务在指定能力内写入/ })
    .check();
  await page.getByRole("button", { name: "授权并开始", exact: true }).click();
  const item = page
    .getByRole("listitem")
    .filter({ has: page.getByText(target, { exact: true }) });
  await expect(item).toContainText("排队中");
  await item.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(item).toContainText("已暂停");
  await item
    .getByRole("button", { name: "续期授权并暂停待确认", exact: true })
    .click();
  await expect(item).toContainText("authorization_updated");
  await item.getByRole("button", { name: "取消", exact: true }).click();
  await expect(item).toContainText("已取消");
  const document = page
    .getByRole("listitem")
    .filter({ hasText: "browser-check.docx" });
  const downloadPromise = page.waitForEvent("download");
  await document.getByRole("button", { name: "下载", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("browser-check.docx");
  await page.getByRole("button", { name: "预览", exact: true }).first().click();
  const preview = page.getByRole("dialog", {
    name: "browser-check.pdf",
    exact: true,
  });
  await expect(preview.getByRole("status")).toHaveText("第 1 页，共 1 页", {
    timeout: 15_000,
  });
  await expect(
    page.getByLabel("文档预览，第 1 页", { exact: true }),
  ).toBeVisible();
  await preview.screenshot({
    path: info.outputPath("agent-preview.png"),
  });
  await page.getByRole("button", { name: "关闭预览", exact: true }).click();
  await expect(preview).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "预览", exact: true }).first(),
  ).toBeFocused();
  await page.reload();
  if (desktop) await page.getByRole("button", { name: /能力与任务/ }).click();
  await expect(
    page.getByRole("listitem").filter({ hasText: target }),
  ).toContainText("已取消");
  await page.screenshot({
    path: info.outputPath("agent-settings.png"),
    fullPage: true,
  });
});
