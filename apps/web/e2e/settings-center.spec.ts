import { expect, test } from "@playwright/test";

for (const width of [1280, 390]) {
  test(`settings navigation and drafts at ${width}px`, async ({
    page,
  }, info) => {
    const desktop = info.project.name.startsWith("settings-desktop");
    const sockets: string[] = [];
    page.on("websocket", (socket) => sockets.push(socket.url()));
    await page.setViewportSize({ width, height: 900 });
    await page.goto(desktop ? "/desktop-settings" : "/settings/models");
    const nav = page.getByRole("navigation", { name: "设置分类" });
    await expect(nav).toBeVisible();
    await page.getByRole("searchbox", { name: "查找设置" }).fill("jev");
    await expect(nav.getByRole("button")).toHaveCount(1);
    await nav.getByRole("button", { name: /^模型 / }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "模型" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "行为决策模型", exact: true })
      .click();
    const provider = page.getByRole("combobox", {
      name: "行为决策模型 Provider",
    });
    await expect(provider).toBeEnabled();
    await provider.selectOption("typesafe");
    await expect(
      page.getByRole("textbox", { name: "行为决策模型 模型 ID" }),
    ).toHaveValue("jev-latest");
    await page
      .getByRole("textbox", { name: "行为决策模型 模型 ID" })
      .fill("jev-draft");
    await expect(
      page.getByText("有未保存的修改", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".settings-brand")).toBeInViewport();
    const brand = await page.locator(".settings-brand").boundingBox();
    expect(brand?.y).toBeGreaterThanOrEqual(0);
    await page.screenshot({
      path: info.outputPath(`settings-models-${width}.png`),
    });

    await nav.getByRole("button", { name: /记忆与资料/ }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "记忆与资料" }),
    ).toBeVisible();
    await nav.getByRole("button", { name: /^模型 / }).click();
    await expect(
      page.getByRole("textbox", { name: "行为决策模型 模型 ID" }),
    ).toHaveValue("jev-draft");

    await nav.getByRole("button", { name: /聊天与渠道/ }).click();
    await page.getByRole("button", { name: "交流与权限", exact: true }).click();
    const freeChat = page.getByRole("switch", { name: "QQ 自由交流" });
    await expect(freeChat).toBeEnabled();
    const previous = await freeChat.isChecked();
    await freeChat.setChecked(!previous);
    await nav.getByRole("button", { name: /声音.*角色语音/ }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "声音" }),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "选择角色语音" }),
    ).toBeVisible();
    await nav.getByRole("button", { name: /聊天与渠道/ }).click();
    await expect(freeChat).toBeChecked({ checked: !previous });
    await page.screenshot({
      path: info.outputPath(`settings-chat-${width}.png`),
    });

    for (const name of [
      /能力与任务.*工具/,
      /扩展与 MCP/,
      /日程与提醒/,
      /连接与诊断/,
    ]) {
      await nav.getByRole("button", { name }).click();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    }
    const layout = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    }));
    expect(layout.width).toBeLessThanOrEqual(layout.viewportWidth + 1);
    expect(layout.height).toBeLessThanOrEqual(layout.viewportHeight + 1);
    expect(sockets.filter((url) => /\/events|\/media/.test(url))).toEqual([]);
  });
}
