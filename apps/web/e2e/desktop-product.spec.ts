import { expect, test } from "@playwright/test";

test("desktop settings is an app-like control surface without chat ownership", async ({
  page,
}, testInfo) => {
  await page.route(
    "http://127.0.0.1:8765/v1/model-configurations",
    async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({
          status: 204,
          headers: {
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Origin": "*",
          },
        });
        return;
      }
      await route.fulfill({
        json: {
          items: [
            modelConfiguration("chat", "openai_compatible", "chat-model"),
            modelConfiguration("memory_extraction", "demo", "memory-demo"),
            modelConfiguration("memory_summary", "demo", "summary-demo"),
            modelConfiguration("embedding", "local_hash", "local-hash-64-v1"),
          ],
        },
        headers: { "Access-Control-Allow-Origin": "*" },
      });
    },
  );
  await page.setViewportSize({ width: 960, height: 700 });
  await page.goto("/desktop-settings");

  await expect(
    page.getByRole("heading", { level: 1, name: "桌宠" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "设置分类" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Conversation" })).toHaveCount(
    0,
  );

  const subtitles = page.getByRole("switch", { name: "显示字幕" });
  await expect(subtitles).toBeEnabled();
  await subtitles.click();
  await expect(subtitles).not.toBeChecked();

  await page.getByRole("button", { name: /声音.*角色语音/ }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "声音" }),
  ).toBeVisible();
  await expect(page.getByText(/设置页不会建立第二条媒体链路/)).toBeVisible();

  await page.getByRole("button", { name: /模型.*AI 与记忆路由/ }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "模型" }),
  ).toBeVisible();
  const modelLayout = await page.evaluate<{
    panelDisplay: string;
    headingDisplay: string;
    panelGap: string;
    cardDisplay: string;
    inputWidth: string;
  }>(`
    (() => {
      const panel = document.querySelector(".model-settings");
      const heading = document.querySelector(".model-settings-heading");
      const card = document.querySelector(".model-role-card");
      const input = card?.querySelector("input:not([type=checkbox])");
      if (!panel || !heading || !card || !input) {
        throw new Error("model settings layout is missing");
      }
      const panelStyle = getComputedStyle(panel);
      return {
        panelDisplay: panelStyle.display,
        headingDisplay: getComputedStyle(heading).display,
        panelGap: panelStyle.gap,
        cardDisplay: getComputedStyle(card).display,
        inputWidth: getComputedStyle(input).width,
      };
    })()
  `);
  expect(modelLayout.panelDisplay).toBe("grid");
  expect(modelLayout.headingDisplay).toBe("flex");
  expect(modelLayout.panelGap).not.toBe("normal");
  expect(modelLayout.cardDisplay).toBe("grid");
  expect(Number.parseFloat(modelLayout.inputWidth)).toBeGreaterThan(200);

  const metrics = await page.evaluate<{
    pageHeight: number;
    viewportHeight: number;
    overflowY: string;
  }>(`
    (() => {
      const scroll = document.querySelector(".desktop-settings-scroll");
      if (!scroll) throw new Error("desktop settings scroll container is missing");
      return {
        pageHeight: document.documentElement.scrollHeight,
        viewportHeight: window.innerHeight,
        overflowY: getComputedStyle(scroll).overflowY,
      };
    })()
  `);
  expect(metrics.pageHeight).toBeLessThanOrEqual(metrics.viewportHeight + 1);
  expect(metrics.overflowY).toBe("auto");

  const screenshot = await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("desktop-settings.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(10_000);
});

function modelConfiguration(
  role: "chat" | "memory_extraction" | "memory_summary" | "embedding",
  provider: "demo" | "openai_compatible" | "local_hash",
  model: string,
) {
  return {
    role,
    provider,
    model,
    base_url:
      provider === "openai_compatible" ? "http://127.0.0.1:9999/v1" : "",
    timeout_seconds: 60,
    context_window: 8192,
    enabled: true,
    api_key_configured: false,
    updated_at: "2026-08-31T00:00:00Z",
  };
}

test("desktop pet reveals its controls and composer while the pointer is over the pet", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 430, height: 650 });
  await page.goto("/desktop-pet");

  const shell = page.locator(".desktop-pet-shell");
  const actions = page.getByRole("navigation", { name: "桌宠操作" });
  const composer = page.getByRole("textbox", { name: "桌宠文字消息" });

  // The physical pointer is shared by the headless browser process and can be
  // left over the new page by the preceding settings test. Establish the
  // outside state explicitly before checking the reveal transition.
  await page.mouse.move(500, 700);
  await expect(shell).toHaveAttribute("data-pointer-inside", "false");
  await expect(actions).toHaveCSS("opacity", "0");
  await expect(actions).toHaveCSS("pointer-events", "none");
  await expect(composer.locator("..")).toHaveCSS("opacity", "0");
  await expect(composer.locator("..")).toHaveCSS("pointer-events", "none");

  await page.getByRole("button", { name: "摸摸绫地宁宁" }).hover({
    position: { x: 200, y: 80 },
  });

  await expect(shell).toHaveAttribute("data-pointer-inside", "true");
  await expect(actions).toHaveCSS("opacity", "1");
  await expect(actions).toHaveCSS("pointer-events", "auto");
  await expect(composer.locator("..")).toHaveCSS("opacity", "1");
  await expect(composer.locator("..")).toHaveCSS("pointer-events", "auto");

  const dialogueOverflow = await page.evaluate<{
    overflowY: string;
    lineClamp: string;
    scrollBehavior: string;
  }>(`
    (() => {
      const container = document.createElement("section");
      container.className = "desktop-pet-dialogue";
      const dialogue = document.createElement("p");
      container.append(dialogue);
      document.body.append(container);
      try {
        const style = getComputedStyle(dialogue);
        return {
          overflowY: style.overflowY,
          lineClamp: style.webkitLineClamp,
          scrollBehavior: style.scrollBehavior,
        };
      } finally {
        container.remove();
      }
    })()
  `);
  expect(dialogueOverflow.overflowY).toBe("auto");
  expect(dialogueOverflow.lineClamp).toBe("none");
  expect(dialogueOverflow.scrollBehavior).toBe("auto");

  const screenshot = await page.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("desktop-pet-hover-composer.png"),
  });
  expect(screenshot.byteLength).toBeGreaterThan(10_000);
});
