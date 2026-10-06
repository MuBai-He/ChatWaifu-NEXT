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
  const desktop = info.project.name.startsWith("channels-desktop");
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

test("settings controls share sizes, keyboard focus and narrow-screen layout", async ({
  page,
  request,
}, info) => {
  expect(
    await (
      await request.get(`${process.env.VITE_RUNTIME_URL}/v1/runtime/health`)
    ).json(),
  ).toMatchObject({ providers: { llm: "demo", tts: "fake", stt: "disabled" } });
  // Two synthetic bindings exercise the account selector without using live accounts.
  const items = [1, 2].map((index) => ({
    configuration: {
      connection_id: `00000000-0000-4000-8000-00000000020${index}`,
      provider_id: "weixin_ilink",
      name: `测试微信 ${index}`,
      account_key: `test-account-${index}`,
      character_id: "default",
      principal_scope: "local",
      enabled: true,
      presentation_policy: {
        profile: "instant_message",
        cadence_enabled: true,
        stickers_enabled: false,
      },
    },
    capabilities: {
      chat_types: ["direct"],
      inbound_message_kinds: ["text", "image"],
      outbound_message_kinds: ["text", "image"],
      supports_typing: true,
    },
    revision: 1,
    status: "ready",
    last_seen_at: null,
    created_at: "2026-10-06T08:00:00+08:00",
    updated_at: "2026-10-06T08:00:00+08:00",
  }));
  const writes: string[] = [];
  page.on("request", (request) => {
    if (["POST", "PUT", "DELETE"].includes(request.method()))
      writes.push(new URL(request.url()).pathname);
  });
  await page.route("**/v1/channel-connections", (route) =>
    route.fulfill({ json: { items, count: items.length } }),
  );
  const desktop = info.project.name.startsWith("channels-desktop");
  await page.setViewportSize({ width: 960, height: 760 });
  await page.goto(desktop ? "/desktop-settings" : "/settings/channels");
  if (desktop)
    await page
      .getByRole("navigation", { name: "设置分类" })
      .getByRole("button", { name: /渠道/ })
      .click();
  const selector = page.getByRole("combobox", {
    name: "管理哪个微信绑定",
    exact: true,
  });
  await expect(selector).toBeEnabled();
  await expect(selector).toHaveCSS("appearance", "none");
  await expect(selector).toHaveCSS("padding-right", "36px");
  await expect(selector).toHaveCSS("border-radius", "10px");
  expect((await selector.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  await page.getByText("回复样式与连接选项", { exact: true }).click();
  const name = page.getByLabel("连接名称", { exact: true });
  const presentation = page.getByRole("combobox", {
    name: "回复形式",
    exact: true,
  });
  await expect(name).toBeEnabled();
  await expect(name).toHaveCSS("font-size", "13px");
  await expect(name).toHaveCSS("border-radius", "10px");
  expect((await name.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  await name.press("Tab");
  await expect(presentation).toBeFocused();
  await presentation.press("Shift+Tab");
  await expect(name).toBeFocused();
  await expect(name).toHaveCSS("outline-style", "solid");
  await expect(name).toHaveCSS("outline-width", "2px");
  const toggle = page.getByRole("switch", { name: "启用微信消息" });
  expect((await toggle.boundingBox())!.height).toBe(22);
  expect((await toggle.boundingBox())!.width).toBe(38);
  await page.screenshot({
    path: info.outputPath("weixin-controls.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "QQ", exact: true }).click();
  await page.getByRole("button", { name: "设置 QQ 连接" }).click();
  const endpoint = page.getByLabel("NapCat WebSocket 地址", { exact: true });
  await expect(endpoint).toHaveCSS("font-size", "13px");
  await expect(endpoint).toHaveCSS("border-radius", "10px");
  expect((await endpoint.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  expect((await endpoint.boundingBox())!.width).toBeGreaterThan(300);
  await page.screenshot({
    path: info.outputPath("qq-controls.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "权限与预算", exact: true }).click();
  await page.getByText("群聊旁听与按需压缩", { exact: true }).click();
  await expect(page.getByLabel("单条字符上限", { exact: true })).toHaveCSS(
    "border-radius",
    "10px",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("budget-controls-mobile.png"),
    fullPage: true,
  });
  // Opening settings creates an empty session; it must never submit a turn or policy.
  expect(writes).toEqual(["/v1/sessions"]);
  if (desktop) {
    await page.setViewportSize({ width: 960, height: 760 });
    const sections = page.getByRole("navigation", { name: "设置分类" });
    await sections.getByRole("button", { name: /陪伴/ }).click();
    const compactNumber = page
      .locator(".companion-settings-number-row input")
      .first();
    await expect(compactNumber).toHaveCSS("width", "84px");
    await expect(compactNumber).toHaveCSS("border-radius", "10px");
    await sections.getByRole("button", { name: /声音/ }).click();
    const compactSelect = page.locator(".desktop-settings-select-row select");
    await expect(compactSelect).toBeVisible();
    expect((await compactSelect.boundingBox())!.width).toBeLessThanOrEqual(310);
    await expect(compactSelect).toHaveCSS("appearance", "none");
    expect((await compactSelect.boundingBox())!.height).toBeGreaterThanOrEqual(
      40,
    );
  }
});

test("conversation scope settings controls preserve names, identity and modal navigation", async ({
  page,
  request,
}, info) => {
  const runtime = process.env.VITE_RUNTIME_URL!;
  const headers = { Authorization: `Bearer ${process.env.VITE_RUNTIME_TOKEN}` };
  const health: unknown = await (
    await request.get(`${runtime}/v1/runtime/health`)
  ).json();
  expect(health).toMatchObject({
    providers: { llm: "demo", tts: "fake", stt: "disabled" },
  });
  const fixture = async (display_name: string) => {
    const response = await request.post(`${runtime}/v1/participants`, {
      headers,
      data: { display_name },
    });
    expect(response.status()).toBe(201);
    return (await response.json()) as {
      participant_id: string;
      display_name: string;
    };
  };
  const suffix = info.project.name;
  const first = await fixture(`测试占位名 · ${suffix}`);
  const second = await fixture(`小周 · ${suffix}`);
  const sceneResponse = await request.post(`${runtime}/v1/scenes`, {
    headers,
    data: {
      display_name: `原有讨论 · ${suffix}`,
      participant_ids: [first.participant_id, second.participant_id],
    },
  });
  expect(sceneResponse.status()).toBe(201);
  const scene = (await sceneResponse.json()) as { scene_id: string };
  const scenesBefore: unknown = await (
    await request.get(`${runtime}/v1/scenes`, { headers })
  ).json();
  const writes: { method: string; path: string }[] = [];
  page.on("request", (r) => {
    if (["POST", "PATCH", "PUT", "DELETE"].includes(r.method()))
      writes.push({ method: r.method(), path: new URL(r.url()).pathname });
  });
  const desktop = info.project.name.startsWith("channels-desktop");
  await page.setViewportSize({ width: 960, height: 700 });
  await page.goto(desktop ? "/desktop-settings" : "/");
  const openConnectionSection = async () => {
    if (desktop)
      await page
        .getByRole("navigation", { name: "设置分类" })
        .getByRole("button", { name: /^连接/ })
        .click();
  };
  await openConnectionSection();
  const opener = page.getByRole("button", {
    name: desktop ? "管理对话" : "切换参与者与场景",
    exact: true,
  });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "参与者与场景" });
  const speaker = dialog.getByRole("combobox", { name: "当前说话者" });
  await expect(speaker).toBeEnabled();
  await speaker.selectOption(first.participant_id);
  await dialog.getByText("修改当前参与者名称", { exact: true }).click();
  const alias = `小林 · ${suffix}`;
  await dialog
    .getByRole("textbox", { name: "显示名称", exact: true })
    .fill(alias);
  await dialog.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(
    dialog.getByText("参与者名称已保存", { exact: true }),
  ).toBeVisible();
  await expect(speaker).toHaveValue(first.participant_id);
  await expect(
    speaker.locator(`option[value="${first.participant_id}"]`),
  ).toHaveText(alias);
  await dialog
    .getByRole("combobox", { name: "对话场景" })
    .selectOption(scene.scene_id);
  await expect(
    dialog
      .getByText(`听众：${alias}、${second.display_name}`, { exact: true })
      .or(
        dialog.getByText(`听众：${second.display_name}、${alias}`, {
          exact: true,
        }),
      ),
  ).toBeVisible();
  expect(
    await (await request.get(`${runtime}/v1/scenes`, { headers })).json(),
  ).toEqual(scenesBefore);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await page.reload();
  await openConnectionSection();
  await opener.click();
  await expect(speaker).toBeEnabled();
  await expect(
    speaker.locator(`option[value="${first.participant_id}"]`),
  ).toHaveText(alias);
  await dialog.getByText("创建共享场景", { exact: true }).click();
  const sceneName = dialog.getByRole("textbox", {
    name: "场景名称",
    exact: true,
  });
  await sceneName.fill("示例共享讨论（未提交）");
  const firstCheckbox = dialog.locator(
    `input[type="checkbox"][value="${first.participant_id}"]`,
  );
  const secondCheckbox = dialog.locator(
    `input[type="checkbox"][value="${second.participant_id}"]`,
  );
  await expect(firstCheckbox).toHaveAccessibleName(alias);
  await expect(secondCheckbox).toHaveAccessibleName(second.display_name);
  await firstCheckbox.check();
  await secondCheckbox.check();
  await expect(
    dialog.getByRole("button", { name: "创建场景", exact: true }),
  ).toBeEnabled();
  for (const control of [
    speaker,
    sceneName,
    dialog.getByRole("button", { name: "应用", exact: true }),
  ]) {
    expect(
      await control.evaluate((node) => {
        const style = getComputedStyle(node);
        return {
          height: node.getBoundingClientRect().height,
          radius: style.borderRadius,
          font: style.fontSize,
        };
      }),
    ).toMatchObject({ height: 40, radius: "10px", font: "13px" });
  }
  expect(
    await speaker.evaluate((node) => getComputedStyle(node).appearance),
  ).toBe("none");
  expect(
    await firstCheckbox.evaluate((node) => node.getBoundingClientRect().width),
  ).toBe(16);
  await dialog.getByRole("button", { name: "应用", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(
    dialog.getByRole("button", { name: "关闭参与者与场景", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(
    dialog.getByRole("button", { name: "应用", exact: true }),
  ).toBeFocused();
  expect(
    await dialog.evaluate((node) => ({
      top: node.getBoundingClientRect().top,
      bottom: node.getBoundingClientRect().bottom,
      viewport: window.innerHeight,
      overflowing: node.scrollWidth > node.clientWidth,
    })),
  ).toMatchObject({ top: 24, bottom: 676, viewport: 700, overflowing: false });
  await page.screenshot({
    path: info.outputPath("conversation-scope-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 640 });
  await expect(
    dialog.getByRole("button", { name: "应用", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(
    await dialog.evaluate((node) => {
      const heading = node.querySelector("header")!.getBoundingClientRect();
      const footer = node.querySelector("footer")!.getBoundingClientRect();
      const body = node.querySelector(".conversation-scope-body")!;
      return (
        heading.top >= 0 &&
        footer.bottom <= window.innerHeight &&
        body.scrollHeight > body.clientHeight
      );
    }),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("conversation-scope-mobile.png"),
  });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  expect(
    writes.filter((r) => /\/turns$|\/channel|\/scenes$/.test(r.path)),
  ).toEqual([]);
  expect(writes.filter((r) => r.method === "PATCH")).toEqual([
    { method: "PATCH", path: `/v1/participants/${first.participant_id}` },
  ]);
});
