import { expect, test } from "@playwright/test";
import { parseChannelRuntimeSettingsResponse } from "@chatwaifu/protocol";

test.skip(
  process.env.CHATWAIFU_E2E_ISOLATED_CHANNELS !== "1",
  "writes require a disposable local Runtime",
);

test("channel settings persist through real Runtime API and reload in both products", async ({
  page,
  request,
}, info) => {
  const runtime = process.env.VITE_RUNTIME_URL!;
  const headers = { Authorization: `Bearer ${process.env.VITE_RUNTIME_TOKEN}` };
  const read = async () =>
    parseChannelRuntimeSettingsResponse(
      await (
        await request.get(`${runtime}/v1/channels/settings`, { headers })
      ).json(),
    );
  const health: unknown = await (
    await request.get(`${runtime}/v1/runtime/health`)
  ).json();
  expect(health).toMatchObject({
    providers: { llm: "demo", tts: "fake", stt: "disabled" },
  });
  const channels: unknown = await (
    await request.get(`${runtime}/v1/channel-connections`, { headers })
  ).json();
  expect(channels).toMatchObject({ count: 0, items: [] });
  const original = await read();
  const sockets: string[] = [];
  const calls: string[] = [];
  page.on("websocket", (socket) => {
    const url = new URL(socket.url());
    // Vite's development reload socket belongs to the test web server.
    if (
      url.host === new URL(runtime).host ||
      /^\/(v1|audio)\//.test(url.pathname)
    )
      sockets.push(`${url.origin}${url.pathname}`);
  });
  page.on("request", (request) => calls.push(request.url()));
  const desktop = info.project.name === "channels-desktop";
  const enter = async () => {
    await page.goto(desktop ? "/desktop-settings" : "/settings/channels");
    if (desktop)
      await page
        .getByRole("navigation", { name: "设置分类" })
        .getByRole("button", { name: /渠道/ })
        .click();
    await expect(
      page.getByRole("heading", { name: "消息渠道", exact: true }),
    ).toBeVisible();
    await expect(page.getByText(runtime, { exact: true })).toBeVisible();
    await expect(page.locator(".conversation-scope-trigger")).toHaveCount(0);
    await expect(page.locator(".runtime-connection-switch")).toHaveCount(0);
  };
  try {
    await enter();
    await expect(
      page.getByRole("button", { name: "扫码绑定微信" }),
    ).toBeEnabled();
    await page.screenshot({
      path: info.outputPath("weixin-settings.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "QQ", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "设置 QQ 连接" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "设置 QQ 连接" }).click();
    await expect(page.getByLabel("NapCat WebSocket 地址")).toBeEnabled();
    await expect(page.getByLabel("NapCat 访问令牌")).toHaveAttribute(
      "type",
      "password",
    );
    await expect(
      page.getByRole("button", { name: "开始 QQ 配对" }),
    ).toBeDisabled();
    await page.screenshot({
      path: info.outputPath("qq-settings.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "权限与预算", exact: true }).click();
    const web = page.getByRole("switch", {
      name: "允许 QQ 主人私聊联网查资料",
    });
    await expect(web).toBeEnabled();
    await web.setChecked(!original.policy.qq_owner_public_web_enabled);
    await page.getByText("群聊旁听与按需压缩", { exact: true }).click();
    const budget = page.getByLabel("旁听输入预算（参考 token）", {
      exact: true,
    });
    const nextBudget =
      original.policy.group_discussion?.input_tokens === 2048 ? 3072 : 2048;
    await budget.fill(String(nextBudget));
    await page.getByRole("button", { name: "保存渠道权限与预算" }).click();
    await expect(page.getByText(/渠道设置已保存到当前 Runtime/)).toBeVisible();
    const saved = await read();
    expect(saved.revision).toBe(original.revision + 1);
    expect(saved.policy).toEqual({
      ...original.policy,
      qq_owner_public_web_enabled: !original.policy.qq_owner_public_web_enabled,
      group_discussion: {
        ...original.policy.group_discussion,
        input_tokens: nextBudget,
      },
    });
    await page.screenshot({
      path: info.outputPath("channel-budgets.png"),
      fullPage: true,
    });
    await page.reload();
    if (desktop)
      await page
        .getByRole("navigation", { name: "设置分类" })
        .getByRole("button", { name: /渠道/ })
        .click();
    await page.getByRole("button", { name: "权限与预算", exact: true }).click();
    await expect(web).toBeChecked({
      checked: !original.policy.qq_owner_public_web_enabled,
    });
    await page.getByText("群聊旁听与按需压缩", { exact: true }).click();
    await expect(budget).toHaveValue(String(nextBudget));
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.locator(".channel-settings-fields").first(),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath("channel-mobile.png"),
      fullPage: true,
    });
    expect(sockets).toEqual([]);
    expect(
      calls.filter((url) => /\/audio\/stream|\/realtime\/|\/turns$/.test(url)),
    ).toEqual([]);
  } finally {
    const latest = await read();
    const restored = await request.put(`${runtime}/v1/channels/settings`, {
      headers,
      data: { expected_revision: latest.revision, policy: original.policy },
    });
    expect(restored.status()).toBe(200);
    expect((await read()).policy).toEqual(original.policy);
  }
});
