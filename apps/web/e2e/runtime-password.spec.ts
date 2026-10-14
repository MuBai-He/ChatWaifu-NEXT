import { expect, test } from "@playwright/test";

const runtime = process.env.PASSWORD_E2E_RUNTIME_URL;
const accessToken = "disposable-password-e2e-access-token-1234567890";
test.skip(
  !runtime,
  "Use playwright.password.config.ts with a fresh disposable Runtime",
);

test("configure, exchange, change and reconnect with a password", async ({
  page,
  request,
}, info) => {
  const password = " 初始浏览器密码 123456 ";
  const changedPassword = " 更新浏览器密码 123456 ";
  const owner = { Authorization: `Bearer ${accessToken}` };
  const status = await request.get(`${runtime}/v1/runtime/password`, {
    headers: owner,
  });
  expect(status.ok()).toBe(true);
  const passwordStatus = (await status.json()) as { configured: boolean };
  expect(passwordStatus.configured).toBe(false);

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/settings/connection");
  await page.getByRole("combobox", { name: "登录方式" }).selectOption("token");
  await page.getByPlaceholder("http://192.168.1.103:18780").fill(runtime!);
  await page.getByPlaceholder("粘贴访问令牌").fill(accessToken);
  await page.getByRole("button", { name: "连接并进入" }).click();
  await expect(
    page.getByText("尚未设置登录密码，请先设置一次。"),
  ).toBeVisible();
  const brand = page.locator('img[src$="brand/chatwaifu-mark-small.png"]');
  await expect(brand).toHaveJSProperty("complete", true);
  expect(
    await brand.evaluate((element: HTMLImageElement) => element.naturalWidth),
  ).toBeGreaterThan(0);
  await page.getByLabel("新密码", { exact: true }).fill(password);
  await page.getByLabel("确认新密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "设置密码", exact: true }).click();
  await expect(page.getByText("已设置登录密码", { exact: true })).toBeVisible();
  const originalSession = await page.evaluate(() =>
    sessionStorage.getItem("chatwaifu.client.connection")!,
  );
  expect(originalSession).not.toContain(password);
  expect(originalSession).not.toContain(accessToken);
  await page.route("**/v1/knowledge/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith("/graph")
      ? {
          nodes: [
            {
              node_id: "person",
              kind: "person",
              label: "测试人物",
              room_id: "owner",
            },
            {
              node_id: "project",
              kind: "project",
              label: "测试项目",
              room_id: "owner",
            },
          ],
          edges: [
            {
              relation_id: "relation",
              source: "person",
              target: "project",
              predicate: "participates",
              description: "测试关系",
              revision: 1,
              status: "confirmed",
              valid_from: null,
              valid_to: null,
              source_refs: [],
            },
          ],
          revision: 1,
          policy_epoch: 1,
          truncated: false,
        }
      : path.endsWith("/status")
        ? {
            records: 2,
            indexed_records: 2,
            policy_epoch: 1,
            index: { done: 2 },
            devices: [],
            enabled: true,
          }
        : { items: [] };
    await route.fulfill({ json: body });
  });
  await page
    .getByRole("navigation", { name: "设置分类" })
    .getByRole("button", { name: /知识图谱/ })
    .click();
  await page.getByRole("link", { name: "打开知识图谱 →" }).click();
  await expect(
    page.getByRole("heading", { name: "知识地图", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: /知识关系图，2 个节点/ }),
  ).toBeVisible();
  await page.getByText("关系列表（1）", { exact: true }).click();
  await expect(
    page.getByRole("button", {
      name: "测试人物 → 测试项目 · 参与",
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("knowledge-from-settings.png"),
  });
  await page.getByRole("link", { name: "返回设置", exact: true }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "知识图谱", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "设置分类" })
    .getByRole("button", { name: /连接与诊断/ })
    .click();
  await page.screenshot({
    path: info.outputPath("password-settings-wide.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("新密码", { exact: true }).scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: info.outputPath("password-settings-mobile.png"),
  });

  await page.evaluate(() =>
    sessionStorage.removeItem("chatwaifu.client.connection"),
  );
  await page.reload();
  await page.getByPlaceholder("输入登录密码").fill("wrong password");
  await page.getByRole("button", { name: "连接并进入" }).click();
  await expect(page.getByRole("alert")).toContainText("密码不正确");
  await page.getByPlaceholder("输入登录密码").fill(password);
  await page.getByRole("button", { name: "连接并进入" }).click();
  await expect(page.getByText("已设置登录密码", { exact: true })).toBeVisible();

  await page.getByLabel("当前密码", { exact: true }).fill(password);
  await page.getByLabel("新密码", { exact: true }).fill(changedPassword);
  await page.getByLabel("确认新密码", { exact: true }).fill(changedPassword);
  await page.getByRole("button", { name: "修改密码", exact: true }).click();
  await expect(page.getByLabel("当前密码", { exact: true })).toHaveValue("");
  const oldConnection = JSON.parse(originalSession) as { token: string };
  const revoked = await request.get(`${runtime}/v1/characters`, {
    headers: { Authorization: `Bearer ${oldConnection.token}` },
  });
  expect(revoked.status()).toBe(401);
  await page.evaluate(() =>
    sessionStorage.removeItem("chatwaifu.client.connection"),
  );
  await page.reload();
  await page.getByPlaceholder("输入登录密码").fill(password);
  await page.getByRole("button", { name: "连接并进入" }).click();
  await expect(page.getByRole("alert")).toContainText("密码不正确");
  await page.getByPlaceholder("输入登录密码").fill(changedPassword);
  await page.getByRole("button", { name: "连接并进入" }).click();
  await expect(page.getByText("已设置登录密码", { exact: true })).toBeVisible();
  const storage = await page.evaluate(() =>
    JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
    }),
  );
  expect(storage).not.toContain(password);
  expect(storage).not.toContain(changedPassword);
});
