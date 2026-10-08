import {
  parseSkillRunSnapshot,
  type SkillDefinition,
} from "@chatwaifu/protocol";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { settingsThemeFindings } from "./settings-theme-audit";

const dimensions = [
  { width: 1870, height: 864, name: "wide" },
  { width: 1548, height: 864, name: "dialog-reference" },
  { width: 1280, height: 900, name: "laptop" },
  { width: 1024, height: 768, name: "compact" },
  { width: 390, height: 844, name: "mobile" },
  { width: 1280, height: 480, name: "short" },
  // A 1280×900 window at 200% browser zoom has this CSS layout viewport.
  { width: 640, height: 450, name: "zoom-200-reflow" },
];

async function enter(page: Page, info: TestInfo, section = "extensions") {
  const desktop = info.project.name.startsWith("settings-desktop");
  await page.goto(desktop ? "/desktop-settings" : `/settings/${section}`);
  const nav = page.getByRole("navigation", { name: "设置分类", exact: true });
  await expect(nav).toBeVisible();
  if (desktop) await nav.getByRole("button", { name: /扩展与 MCP/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  return nav;
}

async function containedControls(page: Page) {
  const body = page.locator(".settings-scroll");
  expect(
    await body.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBe(true);
  const shortButtons = await body
    .locator('button:not([role="switch"])')
    .evaluateAll((elements) =>
      elements
        .filter(
          (element) =>
            element.getClientRects().length &&
            element.getBoundingClientRect().height < 39.9,
        )
        .map((element) => element.textContent),
    );
  expect(shortButtons).toEqual([]);
  expect(await settingsThemeFindings(page)).toEqual([]);
}

test("sidebar columns stay aligned for every label and selection", async ({
  page,
}, info) => {
  const nav = await enter(page, info);
  for (const size of dimensions.filter((size) => size.width > 700)) {
    await page.setViewportSize(size);
    const columns = await nav.locator("button").evaluateAll((buttons) =>
      buttons.map((button) => ({
        icon: button.querySelector("svg")!.getBoundingClientRect().left,
        title: button.querySelector("strong")!.getBoundingClientRect().left,
        description: button.querySelector("small")!.getBoundingClientRect()
          .left,
      })),
    );
    expect(
      Math.max(...columns.map((column) => column.icon)) -
        Math.min(...columns.map((column) => column.icon)),
    ).toBeLessThanOrEqual(1);
    expect(
      Math.max(...columns.map((column) => column.title)) -
        Math.min(...columns.map((column) => column.title)),
    ).toBeLessThanOrEqual(1);
    expect(
      columns.every(
        (column) => Math.abs(column.title - column.description) <= 1,
      ),
    ).toBe(true);
  }
});

test("dark plum settings match the connection screen reference", async ({
  page,
}, info) => {
  await enter(page, info);
  const theme = await page.locator(".settings-shell").evaluate((element) => {
    const css = getComputedStyle(element);
    return {
      scheme: css.colorScheme,
      background: css.backgroundColor,
      foreground: css.color,
    };
  });
  expect(theme).toEqual({
    scheme: "dark",
    background: "rgb(21, 17, 25)",
    foreground: "rgb(238, 229, 239)",
  });
});

test("all settings categories and expanded forms have readable dark surfaces", async ({
  page,
}, info) => {
  test.setTimeout(120_000);
  const nav = await enter(page, info);
  const findings: unknown[] = [];
  for (const size of [dimensions[0], dimensions[4]]) {
    await page.setViewportSize(size);
    for (let i = 0; i < (await nav.getByRole("button").count()); i++) {
      await nav.getByRole("button").nth(i).click();
      const title = await page.getByRole("heading", { level: 1 }).innerText();
      const body = page.locator(".settings-scroll");
      for (const summary of await body
        .locator("details:not([open]) > summary")
        .all()) {
        if (await summary.isVisible()) await summary.click();
      }
      for (const fraction of [0, 0.5, 1]) {
        await body.evaluate((element, value) => {
          element.scrollTop =
            (element.scrollHeight - element.clientHeight) * value;
        }, fraction);
        findings.push(
          ...(await settingsThemeFindings(page)).map((finding) => ({
            category: title,
            viewport: size.name,
            ...finding,
          })),
        );
      }
      await body.evaluate((element) => {
        element.scrollTop = 0;
      });
      await page.screenshot({
        path: info.outputPath(`${size.name}-audit-${i}.png`),
      });
    }
  }
  await info.attach("theme-findings", {
    body: JSON.stringify(findings, null, 2),
    contentType: "application/json",
  });
  expect(findings).toEqual([]);
});

test("voice provider fields and actions share settings styling", async ({
  page,
}, info) => {
  const nav = await enter(page, info, "voice");
  if (info.project.name.startsWith("settings-desktop"))
    await nav.getByRole("button", { name: /^声音 / }).click();
  const panel = page.getByRole("region", { name: "TTS Provider 设置" });
  await expect(
    panel.getByRole("combobox", { name: "TTS 配置入口" }),
  ).toBeEnabled();
  for (const size of dimensions) {
    await page.setViewportSize({ width: size.width, height: size.height });
    const save = panel.getByRole("button", { name: "保存配置", exact: true });
    await save.scrollIntoViewIfNeeded();
    await expect(save).toBeInViewport();
    const actionLayout = await panel
      .locator("footer button")
      .evaluateAll((buttons) =>
        buttons.map((button) => {
          const style = getComputedStyle(button);
          const rect = button.getBoundingClientRect();
          return {
            x: rect.x,
            right: rect.right,
            top: rect.top,
            height: rect.height,
            radius: parseFloat(style.borderRadius),
            border: style.borderTopStyle,
            appearance: style.appearance,
          };
        }),
      );
    expect(actionLayout).toHaveLength(2);
    expect(
      actionLayout.every(
        (button) => button.radius >= 10 && button.height >= 40,
      ),
    ).toBe(true);
    expect(actionLayout[1].x - actionLayout[0].right).toBeGreaterThanOrEqual(8);
    const fields = await panel
      .locator(".tts-configuration-fields > label")
      .evaluateAll((labels) =>
        labels.map((label) => {
          const caption = label.querySelector("span")!;
          const control = label.querySelector("input, select, textarea")!;
          const field = label.getBoundingClientRect();
          const title = caption.getBoundingClientRect();
          const input = control.getBoundingClientRect();
          return {
            gap: input.top - title.bottom,
            inside:
              input.left >= field.left - 1 && input.right <= field.right + 1,
          };
        }),
      );
    expect(fields.length).toBeGreaterThan(5);
    expect(fields.every((field) => field.gap >= 6 && field.inside)).toBe(true);
    await expect(page.getByRole("heading", { level: 1 })).toBeInViewport();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollHeight <= innerHeight + 1,
      ),
    ).toBe(true);
    await containedControls(page);
    await page.screenshot({
      path: info.outputPath(`${size.name}-voice-controls.png`),
    });
  }
  const save = panel.getByRole("button", { name: "保存配置", exact: true });
  await save.hover();
  const hoverStyle = await save.evaluate((element) => {
    const css = getComputedStyle(element);
    return { background: css.backgroundColor, foreground: css.color };
  });
  expect(hoverStyle).toEqual({
    background: "rgb(137, 86, 126)",
    foreground: "rgb(255, 248, 253)",
  });
});

test("settings retain the plum palette and space below the floating header", async ({
  page,
}, info) => {
  const nav = await enter(page, info, "channels");
  if (info.project.name.startsWith("settings-desktop"))
    await nav.getByRole("button", { name: /^聊天与渠道 / }).click();
  for (const size of dimensions) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.locator(".settings-scroll").evaluate((element) => {
      element.scrollTop = 0;
    });
    const layout = await page.evaluate(() => {
      const shell = document.querySelector(".settings-shell")!;
      const header = document
        .querySelector(".settings-heading-inner")!
        .getBoundingClientRect();
      const intro = document
        .querySelector(
          ".settings-scroll .settings-content-frame > div:not([hidden]) .desktop-settings-section-intro",
        )!
        .getBoundingClientRect();
      return {
        brand: getComputedStyle(document.documentElement)
          .getPropertyValue("--plum-accent")
          .trim(),
        settings: getComputedStyle(shell)
          .getPropertyValue("--settings-accent")
          .trim(),
        headerTop: header.top,
        gap: intro.top - header.bottom,
        documentScroll: document.documentElement.scrollTop,
      };
    });
    expect(layout.settings).toBe(layout.brand);
    expect(layout.documentScroll).toBe(0);
    expect(layout.headerTop).toBeGreaterThanOrEqual(size.width > 700 ? 32 : 12);
    expect(layout.gap).toBeGreaterThanOrEqual(size.width > 700 ? 32 : 24);
    await expect(page.getByRole("heading", { level: 1 })).toBeInViewport();
  }
});

test.beforeEach(async ({ request }) => {
  expect(
    await (
      await request.get(`${process.env.VITE_RUNTIME_URL}/v1/runtime/health`)
    ).json(),
  ).toMatchObject({
    providers: { llm: "demo", tts: "fake", stt: "disabled" },
  });
});

for (const size of dimensions) {
  test(`all categories align and remain reachable: ${size.name}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    const media: string[] = [];
    page.on("websocket", (socket) => {
      if (/\/events|\/media/.test(socket.url())) media.push(socket.url());
    });
    page.on("request", (request) => {
      if (/\/audio\/stream|\/realtime\/sessions|\/turns$/.test(request.url()))
        media.push(request.url());
    });
    const nav = await enter(page, info);
    const count = await nav.getByRole("button").count();
    for (let index = 0; index < count; index++) {
      await nav.getByRole("button").nth(index).click();
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toBeVisible();
      const frames = await page
        .locator(".settings-content-frame")
        .evaluateAll((elements) =>
          elements.map((element) => {
            const rect = element.getBoundingClientRect();
            return { x: rect.x, width: rect.width };
          }),
        );
      expect(Math.abs(frames[0].x - frames[1].x)).toBeLessThanOrEqual(1);
      expect(Math.abs(frames[0].width - frames[1].width)).toBeLessThanOrEqual(
        1,
      );
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
      const body = page.locator(".settings-scroll");
      expect(
        await body.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      ).toBe(true);
      const shortButtons = await body
        .locator('button:not([role="switch"])')
        .evaluateAll((elements) =>
          elements
            .filter(
              (element) =>
                element.getClientRects().length &&
                element.getBoundingClientRect().height < 39.9,
            )
            .map((element) => element.textContent),
        );
      expect(shortButtons).toEqual([]);
      expect(await settingsThemeFindings(page)).toEqual([]);
      await body.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      expect(
        await body.evaluate(
          (element) =>
            element.scrollHeight - element.scrollTop - element.clientHeight,
        ),
      ).toBeLessThanOrEqual(1);
      const section = await heading.innerText();
      if (["聊天与渠道", "扩展与 MCP", "模型"].includes(section)) {
        await body.evaluate((element) => {
          element.scrollTop = 0;
        });
        await page.screenshot({
          path: info.outputPath(`${size.name}-${index}.png`),
        });
      }
      if (section === "聊天与渠道") {
        await page.getByRole("button", { name: "QQ", exact: true }).click();
        await page.getByRole("button", { name: "设置 QQ 连接" }).waitFor();
        await containedControls(page);
        await page
          .getByRole("button", { name: "交流与权限", exact: true })
          .click();
        const save = page.getByRole("button", { name: "保存交流与权限" });
        await save.scrollIntoViewIfNeeded();
        await expect(save).toBeInViewport();
        await containedControls(page);
      }
      if (section === "扩展与 MCP") {
        await page.getByRole("tab", { name: "插件", exact: true }).click();
        await expect(
          page.getByRole("textbox", { name: "本地插件目录" }),
        ).toBeVisible();
        await containedControls(page);
        await page.getByRole("tab", { name: "MCP", exact: true }).click();
        await expect(
          page.getByRole("textbox", { name: "MCP 连接名称" }),
        ).toBeEnabled();
        const save = page.getByRole("button", {
          name: "保存连接",
          exact: true,
        });
        await save.scrollIntoViewIfNeeded();
        await expect(save).toBeInViewport();
        await containedControls(page);
        await body.evaluate((element) => {
          element.scrollTop = 0;
        });
        await page.screenshot({
          path: info.outputPath(`${size.name}-mcp.png`),
        });
      }
    }
    expect(media).toEqual([]);
  });
}

test("the bound QQ account surface aligns in wide and narrow layouts", async ({
  page,
}, info) => {
  await page.route("**/v1/channel-connections", (route) => {
    return route.fulfill({
      json: {
        count: 1,
        items: [
          {
            configuration: {
              connection_id: "00000000-0000-4000-8000-000000000202",
              provider_id: "qq_napcat",
              name: "视觉验收用 QQ",
              character_id: "default",
              principal_scope: "local",
              enabled: true,
            },
            revision: 1,
            status: "ready",
            last_seen_at: null,
            created_at: "2026-10-03T11:00:00+08:00",
            updated_at: "2026-10-03T11:00:00+08:00",
          },
        ],
      },
    });
  });
  const nav = await enter(page, info, "channels");
  if (info.project.name.startsWith("settings-desktop"))
    await nav.getByRole("button", { name: /聊天与渠道/ }).click();
  await page.getByRole("button", { name: "QQ", exact: true }).click();
  await expect(
    page.getByRole("switch", { name: "启用 QQ 连接" }),
  ).toBeEnabled();
  for (const size of dimensions.slice(0, 5)) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await containedControls(page);
    await page.locator(".settings-scroll").evaluate((element) => {
      element.scrollTop = 0;
    });
    await page.screenshot({
      path: info.outputPath(`${size.name}-qq-bound-fixture.png`),
    });
  }
});

test("embedded extensions keep drafts, clear MCP secrets and stop hidden polling", async ({
  page,
}, info) => {
  const requests: string[] = [];
  page.on("request", (request) =>
    requests.push(new URL(request.url()).pathname),
  );
  await page.clock.install();
  const nav = await enter(page, info);
  const list = page.getByRole("complementary", { name: "Skills 列表" });
  const last = list.getByRole("button").last();
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeInViewport();
  await last.click();
  await page
    .getByRole("searchbox", { name: "查找 Skill" })
    .fill("runtime.status");
  await list.getByRole("button", { name: "选择 Runtime Status" }).click();
  await page.getByRole("button", { name: /read.*只读/ }).click();
  const args = page.getByRole("textbox", { name: "Skill JSON 参数" });
  await args.fill('{"draft":true}');
  await page.getByRole("tab", { name: "插件", exact: true }).click();
  const path = page.getByRole("textbox", { name: "本地插件目录" });
  await path.fill("/long/path/".repeat(18));
  await nav.getByRole("button", { name: /^模型 / }).click();
  const before = requests.filter((url) =>
    /\/skills|\/plugins|\/skill-runs|\/skill-confirmations/.test(url),
  ).length;
  await page.clock.fastForward(46_000);
  expect(
    requests.filter((url) =>
      /\/skills|\/plugins|\/skill-runs|\/skill-confirmations/.test(url),
    ),
  ).toHaveLength(before);
  await nav.getByRole("button", { name: /扩展与 MCP/ }).click();
  await expect(path).toHaveValue("/long/path/".repeat(18));
  await page.getByRole("tab", { name: "Skills", exact: true }).click();
  await expect(args).toHaveValue('{"draft":true}');
  await page.getByRole("tab", { name: "MCP", exact: true }).click();
  const name = page.getByRole("textbox", { name: "MCP 连接名称", exact: true });
  await expect(name).toBeEnabled();
  await name.fill("MCP 草稿");
  await page.getByLabel("MCP 传输类型").selectOption("streamable_http");
  const bearer = page.getByLabel("MCP Bearer Token");
  await bearer.fill("disposable-secret");
  await page.getByRole("tab", { name: "Skills", exact: true }).click();
  await page.getByRole("tab", { name: "MCP", exact: true }).click();
  await expect(name).toHaveValue("MCP 草稿");
  await expect(bearer).toHaveValue("");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

for (const presentation of ["embedded", "dialog"] as const) {
  test(`write confirmation has one focus owner and denial reaches Runtime: ${presentation}`, async ({
    page,
    request,
  }, info) => {
    test.skip(
      presentation === "dialog" &&
        info.project.name.startsWith("settings-desktop"),
      "The shared Web chat shortcut owns this dialog",
    );
    await page.clock.install();
    const nav = await enter(page, info);
    // Keep the capability panel mounted to detect duplicate confirmation owners.
    await nav.getByRole("button", { name: /^能力与任务 / }).click();
    await nav.getByRole("button", { name: /扩展与 MCP/ }).click();
    await page.getByRole("tab", { name: "插件", exact: true }).click();
    let runId: string | null = null;
    try {
      await page
        .getByRole("button", { name: "安装 Local Echo 测试插件" })
        .click();
      await expect(
        page.getByText("Local Echo MCP", { exact: true }),
      ).toBeVisible();
      if (presentation === "dialog") {
        await page.goto("/");
        await page
          .getByRole("button", { name: "Skills & 插件", exact: true })
          .click();
      } else {
        await page.getByRole("tab", { name: "Skills", exact: true }).click();
      }
      await page
        .getByRole("searchbox", { name: "查找 Skill" })
        .fill("local.echo");
      await page
        .getByRole("button", { name: "选择 Local Echo", exact: true })
        .click();
      await page.getByRole("button", { name: /append_note.*写入/ }).click();
      const submission = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          /\/sessions\/[^/]+\/skill-runs$/.test(response.url()),
      );
      await page
        .getByRole("button", { name: "运行 Skill", exact: true })
        .click();
      const run = parseSkillRunSnapshot(await (await submission).json());
      runId = run.skill_run_id;
      expect(run.state).toBe("waiting_for_confirmation");
      await page.clock.fastForward(16_000);
      const prompt = page.getByRole("alertdialog", { name: "需要你的确认" });
      await expect(prompt).toHaveCount(1);
      await expect(
        prompt.getByRole("button", { name: "拒绝", exact: true }),
      ).toBeFocused();
      await expect(prompt.getByLabel("将发送的参数")).toContainText(
        "本地测试笔记",
      );
      expect(await settingsThemeFindings(page)).toEqual([]);
      await page.keyboard.press("Escape");
      await expect(prompt).toBeVisible();
      if (presentation === "dialog")
        await expect(page.locator(".settings-dialog-overlay")).toHaveCount(1);
      const decision = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response
            .url()
            .endsWith(`/skill-confirmations/${run.confirmation_request_id}`),
      );
      await prompt.getByRole("button", { name: "拒绝", exact: true }).click();
      const denied = parseSkillRunSnapshot(await (await decision).json());
      expect(denied.state).toBe("failed");
      expect(denied.error?.code).toBe("permission_denied");
      await expect(prompt).toHaveCount(0);
      if (presentation === "dialog") {
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(
          page.getByRole("button", { name: "Skills & 插件", exact: true }),
        ).toBeFocused();
      }
    } finally {
      if (runId)
        await request.post(
          `${process.env.VITE_RUNTIME_URL}/v1/skill-runs/${runId}/cancel`,
          {
            headers: {
              Authorization: `Bearer ${process.env.VITE_RUNTIME_TOKEN}`,
            },
            data: {},
          },
        );
      const removed = await request.delete(
        `${process.env.VITE_RUNTIME_URL}/v1/plugins/local.echo`,
        {
          headers: {
            Authorization: `Bearer ${process.env.VITE_RUNTIME_TOKEN}`,
          },
        },
      );
      expect(removed.ok()).toBe(true);
    }
  });
}

test("catalog loading, failure, empty and long metadata remain readable", async ({
  page,
}, info) => {
  let state: "loading" | "failure" | "empty" | "long" = "loading";
  let finish!: () => void;
  const released = new Promise<void>((resolve) => {
    finish = resolve;
  });
  await page.route("**/v1/skills", async (route) => {
    if (state === "loading") await released;
    if (state === "failure")
      return route.fulfill({
        status: 503,
        json: { detail: "隔离测试：目录暂时不可用" },
      });
    if (state === "long") {
      const response = await route.fetch();
      const catalog = (await response.json()) as {
        items: SkillDefinition[];
        count: number;
      };
      catalog.items[0].name = "很长的能力名称与连续标识".repeat(16);
      catalog.items[0].description = "LongMetadataWithoutBreaks".repeat(60);
      return route.fulfill({ response, json: catalog });
    }
    return route.fulfill({ json: { items: [], count: 0 } });
  });
  await enter(page, info);
  await expect(page.getByText("正在读取能力与插件…")).toBeVisible();
  state = "failure";
  finish();
  await expect(page.getByRole("alert")).toContainText("目录暂时不可用");
  expect(await settingsThemeFindings(page)).toEqual([]);
  state = "empty";
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByText("暂无可用 Skills")).toBeVisible();
  expect(await settingsThemeFindings(page)).toEqual([]);
  state = "long";
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("article", { name: "Skill 详情" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page
      .locator(".settings-scroll")
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: info.outputPath("long-metadata-mobile.png") });
});

test("reduced motion and increased contrast use opaque readable controls", async ({
  page,
}, info) => {
  await page.emulateMedia({ reducedMotion: "reduce", contrast: "more" });
  await enter(page, info);
  const style = await page.locator(".settings-sidebar").evaluate((element) => {
    const css = getComputedStyle(element);
    return {
      glass: css.getPropertyValue("--settings-glass").trim(),
      background: css.backgroundColor,
      text: css.color,
      transition: css.transitionDuration,
    };
  });
  expect(style.glass).toBe("#261e2c");
  expect(style.background).toBe("rgb(38, 30, 44)");
  expect(style.text).toBe("rgb(238, 229, 239)");
  expect(style.transition).toBe("0s");
  await page.getByRole("tab", { name: "Skills", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    page.getByRole("tab", { name: "插件", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("End");
  await expect(
    page.getByRole("tab", { name: "MCP", exact: true }),
  ).toBeFocused();
});

test("chat shortcuts share centered dialogs, trap focus and restore the opener", async ({
  page,
}, info) => {
  test.skip(
    info.project.name.startsWith("settings-desktop"),
    "Web chat owns these shared shortcuts; desktop embedded panels are covered separately",
  );
  await page.setViewportSize({ width: 1548, height: 864 });
  await page.goto("/");
  const opener = page.getByRole("button", {
    name: "Skills & 插件",
    exact: true,
  });
  await expect(opener).toBeEnabled();
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Skills 与插件控制中心" });
  await expect(dialog).toBeVisible();
  const close = dialog.getByRole("button", { name: "关闭 Skills 控制中心" });
  await expect(close).toBeFocused();
  const rect = await dialog.boundingBox();
  expect(Math.abs(rect!.x + rect!.width / 2 - 774)).toBeLessThanOrEqual(1);
  expect(Math.abs(rect!.y + rect!.height / 2 - 432)).toBeLessThanOrEqual(1);
  const contrast = await dialog.evaluate((element) => {
    const channels = (color: string) => color.match(/[\d.]+/g)!.map(Number);
    const background = channels(getComputedStyle(element).backgroundColor);
    const foreground = channels(
      getComputedStyle(element.querySelector(".settings-dialog-heading p")!)
        .color,
    );
    const luminance = (rgb: number[]) =>
      rgb
        .slice(0, 3)
        .map((value) => {
          const channel = value / 255;
          return channel <= 0.04045
            ? channel / 12.92
            : ((channel + 0.055) / 1.055) ** 2.4;
        })
        .reduce(
          (sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index],
          0,
        );
    // The lightest possible scene behind the material must keep caption text readable.
    const backing = luminance(
      background
        .slice(0, 3)
        .map(
          (value) =>
            value * (background[3] ?? 1) + 255 * (1 - (background[3] ?? 1)),
        ),
    );
    const text = luminance(foreground);
    return (Math.max(backing, text) + 0.05) / (Math.min(backing, text) + 0.05);
  });
  expect(contrast).toBeGreaterThanOrEqual(4.5);
  expect(await settingsThemeFindings(page)).toEqual([]);
  await page.keyboard.press("Shift+Tab");
  expect(
    await dialog.evaluate((element) =>
      element.contains(document.activeElement),
    ),
  ).toBe(true);
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await page.screenshot({ path: info.outputPath("skills-dialog.png") });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await page.setViewportSize({ width: 390, height: 480 });
  await opener.click();
  await dialog.getByRole("tab", { name: "插件", exact: true }).click();
  await dialog
    .getByRole("button", { name: "安装 Local Echo 测试插件" })
    .scrollIntoViewIfNeeded();
  await expect(
    dialog.getByRole("button", { name: "安装 Local Echo 测试插件" }),
  ).toBeInViewport();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "记忆中心", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "结构化记忆中心" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "记忆中心", exact: true }),
  ).toBeFocused();
});

test("offline state recovers and the opaque blur fallback stays usable", async ({
  page,
}, info) => {
  await page.route("**/v1/runtime/health", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "disposable offline state" },
    }),
  );
  await enter(page, info);
  await expect(page.locator(".settings-connection")).toHaveText("连接已断开");
  await expect(page.getByRole("button", { name: "立即重连" })).toBeVisible();
  await page.unroute("**/v1/runtime/health");
  await page.getByRole("button", { name: "立即重连" }).click();
  await expect(page.locator(".settings-connection")).toHaveText("已连接");
  await expect(
    page.getByRole("button", { name: "选择 Runtime Status" }),
  ).toBeVisible();
  // Apply the actual @supports fallback declarations even in capable engines.
  const fallback = await page.evaluate(() => {
    const collect = (rules: CSSRuleList): string[] =>
      [...rules].flatMap((rule) => {
        if (
          rule instanceof CSSSupportsRule &&
          rule.conditionText.startsWith("not") &&
          rule.conditionText.includes("backdrop-filter")
        )
          return [...rule.cssRules].map((child) => child.cssText);
        if (rule instanceof CSSGroupingRule) return collect(rule.cssRules);
        return [];
      });
    return [...document.styleSheets]
      .flatMap((sheet) => collect(sheet.cssRules))
      .join("\n");
  });
  expect(fallback).toContain("--settings-glass");
  await page.addStyleTag({
    content: `${fallback}\n.settings-material * { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }`,
  });
  expect(
    await page
      .locator(".settings-sidebar")
      .evaluate((element) => getComputedStyle(element).backgroundColor),
  ).toBe("rgb(38, 30, 44)");
  await page.screenshot({ path: info.outputPath("opaque-fallback.png") });
});

test("hidden model index polling stops and resumes without overwriting drafts", async ({
  page,
}, info) => {
  let calls = 0;
  await page.clock.install();
  await page.route("**/v1/indexes/rebuild", (route) => {
    calls++;
    return route.fulfill({
      json: {
        schema_version: "1.0",
        job_id: "visual-running-fixture",
        state: "running",
        domains: {},
      },
    });
  });
  const nav = await enter(page, info);
  await nav.getByRole("button", { name: /^模型 / }).click();
  const input = page.getByLabel("聊天模型 模型 ID");
  await input.fill("retained-model-draft");
  await page.clock.fastForward(600);
  await expect.poll(() => calls).toBeGreaterThan(1);
  await nav.getByRole("button", { name: /扩展与 MCP/ }).click();
  const before = calls;
  await page.clock.fastForward(5100);
  expect(calls).toBe(before);
  await nav.getByRole("button", { name: /^模型 / }).click();
  await page.clock.fastForward(600);
  await expect.poll(() => calls).toBeGreaterThan(before);
  await expect(input).toHaveValue("retained-model-draft");
});

test("touch controls reach 44px and hidden schedule polling stops", async ({
  browser,
}, info) => {
  const context = await browser.newContext({
    baseURL: info.project.use.baseURL,
    viewport: { width: 390, height: 844 },
    hasTouch: true,
  });
  try {
    const page = await context.newPage();
    const calls: string[] = [];
    page.on("request", (request) =>
      calls.push(new URL(request.url()).pathname),
    );
    await page.clock.install();
    const nav = await enter(page, info);
    await page.getByRole("button", { name: "选择 Runtime Status" }).waitFor();
    const controls = await page
      .locator(
        ".extensions-tabs button, .extensions-toolbar button, .extensions-search input",
      )
      .evaluateAll((elements) =>
        elements.map((element) => element.getBoundingClientRect().height),
      );
    expect(controls.every((height) => height >= 44)).toBe(true);
    await nav.getByRole("button", { name: /日程与提醒/ }).click();
    if (info.project.name.startsWith("settings-desktop"))
      await page.getByText("Apple 设备与桌宠闹钟", { exact: true }).click();
    await page.getByRole("button", { name: "保存任务", exact: true }).waitFor();
    await page.clock.fastForward(6000);
    await nav.getByRole("button", { name: /^模型 / }).click();
    const before = calls.filter((url) =>
      /\/personal-assistant\/(organizer|destinations)/.test(url),
    ).length;
    await page.clock.fastForward(16_000);
    expect(
      calls.filter((url) =>
        /\/personal-assistant\/(organizer|destinations)/.test(url),
      ),
    ).toHaveLength(before);
  } finally {
    await context.close();
  }
});

test("populated MCP, model roles and organizer subpages remain readable", async ({
  page,
}, info) => {
  const connectionId = "00000000-0000-4000-8000-000000000222";
  await page.route("**/v1/mcp/connections", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            connection_id: connectionId,
            name: "长名称与路径检查".repeat(10),
            transport: "streamable_http",
            url:
              "http://127.0.0.1:9000/" + "very-long-service-path/".repeat(15),
            status: "error",
            last_error: "ConnectionUnavailable".repeat(30),
            capabilities: { connection_id: connectionId },
            created_at: "2026-10-08T00:00:00Z",
            updated_at: "2026-10-08T00:00:00Z",
          },
        ],
      },
    }),
  );
  const nav = await enter(page, info);
  for (const size of [dimensions[0], dimensions[4]]) {
    await page.setViewportSize(size);
    await nav.getByRole("button", { name: /扩展与 MCP/ }).click();
    await page.getByRole("tab", { name: "MCP", exact: true }).click();
    const item = page.locator(".mcp-connection-list article > button");
    await expect(item).toHaveCount(1);
    const layout = await item.evaluate((element) => {
      const name = element.querySelector("span")!.getBoundingClientRect();
      const metadata = element.querySelector("small")!.getBoundingClientRect();
      return {
        gap: metadata.top - name.bottom,
        aligned: Math.abs(name.left - metadata.left) <= 1,
      };
    });
    expect(layout.gap).toBeGreaterThanOrEqual(4);
    expect(layout.aligned).toBe(true);
    await containedControls(page);
    await page.screenshot({
      path: info.outputPath(`${size.name}-populated-mcp.png`),
    });
    await nav.getByRole("button", { name: /^模型 / }).click();
    for (const tab of await page
      .locator(".model-role-navigation button")
      .all()) {
      await tab.click();
      await containedControls(page);
    }
    await nav.getByRole("button", { name: /日程与提醒/ }).click();
    if (info.project.name.startsWith("settings-desktop"))
      await page.getByText("Apple 设备与桌宠闹钟", { exact: true }).click();
    for (const tab of await page
      .getByRole("navigation", { name: "日程与提醒功能", exact: true })
      .getByRole("button")
      .all()) {
      await tab.click();
      await containedControls(page);
    }
  }
});

test("auxiliary dialogs inherit dark controls and fit short windows", async ({
  page,
}, info) => {
  const nav = await enter(page, info);
  const desktop = info.project.name.startsWith("settings-desktop");
  for (const size of [
    { width: 1280, height: 480 },
    { width: 390, height: 480 },
  ]) {
    await page.setViewportSize(size);
    if (desktop) {
      await page.getByRole("button", { name: /新手引导/ }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible();
      await page.keyboard.press("Shift+Tab");
      expect(
        await dialog.evaluate((element) =>
          element.contains(document.activeElement),
        ),
      ).toBe(true);
      const dismiss = dialog.getByRole("button", {
        name: "以后再说",
        exact: true,
      });
      await dismiss.scrollIntoViewIfNeeded();
      await expect(dismiss).toBeInViewport();
      expect(await settingsThemeFindings(page)).toEqual([]);
      await dismiss.click();
      await expect(
        page.getByRole("button", { name: /新手引导/ }),
      ).toBeFocused();
      await nav.getByRole("button", { name: /本地数据与模型包/ }).click();
      await page
        .getByRole("button", { name: "清除当前数据", exact: true })
        .click();
      const clear = page.getByRole("dialog", { name: "清除当前对话与记忆" });
      const cancel = clear.getByRole("button", { name: "取消", exact: true });
      await cancel.scrollIntoViewIfNeeded();
      await expect(cancel).toBeInViewport();
      expect(await settingsThemeFindings(page)).toEqual([]);
      await clear
        .getByRole("button", { name: "我已了解，继续", exact: true })
        .click();
      await expect(
        clear.getByRole("button", { name: "永久清除", exact: true }),
      ).toBeDisabled();
      expect(await settingsThemeFindings(page)).toEqual([]);
      await page.screenshot({
        path: info.outputPath(`${size.width}-clear-confirmation.png`),
      });
      await cancel.click();
    } else {
      await page.goto("/");
      await page
        .getByRole("button", { name: "切换参与者与场景", exact: true })
        .click();
      const dialog = page.getByRole("dialog", { name: "参与者与场景" });
      await expect(dialog).toBeVisible();
      expect(await settingsThemeFindings(page)).toEqual([]);
      await page.screenshot({
        path: info.outputPath(`${size.width}-participant-dialog.png`),
      });
      await page
        .getByRole("button", { name: "关闭参与者与场景", exact: true })
        .click();
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  }
});

test("expanded task fields use full-width vertical alignment", async ({
  page,
}, info) => {
  const nav = await enter(page, info);
  await nav.getByRole("button", { name: /^能力与任务 / }).click();
  const task = page
    .locator(".agent-settings > details")
    .filter({ has: page.locator("summary", { hasText: "创建授权任务" }) });
  await task.locator("summary").click();
  for (const size of dimensions) {
    await page.setViewportSize(size);
    const fields = await task
      .locator("label:not(:has(input[type=checkbox]))")
      .evaluateAll((labels) =>
        labels.map((label) => {
          const control = label
            .querySelector("input, textarea")!
            .getBoundingClientRect();
          const field = label.getBoundingClientRect();
          return {
            width: Math.abs(control.width - field.width),
            gap: control.top - field.top,
            inside: control.right <= field.right + 1,
          };
        }),
      );
    expect(fields).toHaveLength(3);
    expect(
      fields.every(
        (field) => field.width <= 1 && field.gap >= 28 && field.inside,
      ),
    ).toBe(true);
    const submit = task.getByRole("button", { name: "授权并开始" });
    await submit.scrollIntoViewIfNeeded();
    await expect(submit).toBeInViewport();
    await containedControls(page);
    await page.screenshot({
      path: info.outputPath(`${size.name}-task-form.png`),
    });
  }
  await nav.getByRole("button", { name: /连接与诊断/ }).click();
  const scope = page.getByRole("button", { name: "管理参与者", exact: true });
  if (await scope.count()) {
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await scope.evaluate((element) => element.getBoundingClientRect().height),
    ).toBeLessThan(50);
  }
});
