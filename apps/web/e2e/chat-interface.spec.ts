import { expect, test } from "@playwright/test";

const preference = "chatwaifu.web.minimal-interface.v1";

for (const width of [1280, 390]) {
  test(`chat menu and minimal preference survive reload at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
    const menu = page.getByRole("navigation", {
      name: "游戏菜单",
      exact: true,
    });
    await expect(menu).toBeVisible();
    const buttons = menu.getByRole("button");
    await expect(buttons).toHaveCount(4);
    const bounds = await buttons.evaluateAll((elements) =>
      elements.map((element) => {
        const { x, y, width, height } = element.getBoundingClientRect();
        return { x, y, width, height };
      }),
    );
    for (const box of bounds) {
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
      expect(box.y + box.height).toBeLessThanOrEqual(845);
    }
    const brand = page.getByRole("link", {
      name: "ChatWaifu NEXT home",
      exact: true,
    });
    await expect(brand).toBeVisible();
    await menu.getByRole("button", { name: /设置$/ }).click();
    const toggle = page.getByRole("switch", { name: "简洁界面", exact: true });
    await expect(toggle).not.toBeChecked();
    await toggle.check();
    await expect(brand).toHaveCount(0);
    await expect(
      page.locator(".vn-character-title, .vn-disclosure"),
    ).toHaveCount(0);
    await expect(
      page.getByRole("textbox", { name: "Message", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate((key) => localStorage.getItem(key), preference),
    ).toBe("true");
    await page.reload();
    await expect(brand).toHaveCount(0);
    await menu.getByRole("button", { name: /设置$/ }).click();
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await expect(brand).toBeVisible();
    await expect(
      page.locator(".vn-character-title, .vn-disclosure"),
    ).toHaveCount(2);
    expect(
      await page.evaluate((key) => localStorage.getItem(key), preference),
    ).toBe("false");
    await page.reload();
    await expect(brand).toBeVisible();
    const draft = page.getByRole("textbox", { name: "Message", exact: true });
    const text = "长草稿保留完整，不发送。\n".repeat(40);
    await draft.fill(text);
    await expect(draft).toHaveValue(text);
    expect(
      await draft.evaluate(
        (element) => element.scrollHeight > element.clientHeight,
      ),
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  });
}

test("minimal mode remains usable when its browser storage is unavailable", async ({
  page,
}) => {
  await page.addInitScript((key) => {
    // eslint-disable-next-line @typescript-eslint/unbound-method -- Called below with the original storage receiver.
    const read = Storage.prototype.getItem;
    // eslint-disable-next-line @typescript-eslint/unbound-method -- Called below with the original storage receiver.
    const write = Storage.prototype.setItem;
    Storage.prototype.getItem = function (name) {
      if (name === key) throw new DOMException("unavailable", "SecurityError");
      return read.call(this, name);
    };
    Storage.prototype.setItem = function (name, value) {
      if (name === key) throw new DOMException("unavailable", "SecurityError");
      return write.call(this, name, value);
    };
  }, preference);
  await page.goto("/");
  const brand = page.getByRole("link", {
    name: "ChatWaifu NEXT home",
    exact: true,
  });
  await expect(brand).toBeVisible();
  await page
    .getByRole("navigation", { name: "游戏菜单" })
    .getByRole("button", { name: /设置$/ })
    .click();
  const toggle = page.getByRole("switch", { name: "简洁界面", exact: true });
  await toggle.check();
  await expect(brand).toHaveCount(0);
  await toggle.uncheck();
  await expect(brand).toBeVisible();
});
