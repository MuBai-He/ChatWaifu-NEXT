import { test, expect, type Page } from "@playwright/test";

const session = "195cf097-5180-4c3b-ad3b-334e14b87f3c";
const source = {
  record_id: "source:fixture",
  room_id: "room:fixture",
  kind: "source",
  title: "青禾项目 · 合成资料",
  text: "小林负责设计稿。评审由周四改到周五。<script>window.fixtureExecuted=true</script>",
  state: "active",
  confirmed: false,
  occurred_at: 1791800000000,
  due_at: null,
  starts_at: null,
  ends_at: null,
  remind_at: null,
  timezone: "Asia/Shanghai",
  source_refs: [],
  sender_id: "person:fixture",
  sender_name: "小林",
  sent_by_owner: false,
  origin: {},
  revision: 1,
  deleted: false,
  updated_at: 1791800000000,
};
const item = {
  ...source,
  record_id: "work:fixture",
  kind: "todo",
  title: "青禾项目评审",
  text: "核对设计稿",
  state: "open",
  confirmed: true,
  source_refs: [{ record_id: source.record_id, revision: 1 }],
};
async function fixture(page: Page, conflict = false) {
  const events: { path: string; body: unknown }[] = [];
  await page.route("**/v1/knowledge/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/v1/knowledge", "");
    const body: unknown = request.postDataJSON();
    events.push({ path, body });
    let response: unknown = {};
    if (path === "/graph")
      response = {
        nodes: [
          {
            node_id: "person:fixture",
            kind: "person",
            label: "小林",
            room_id: "room:fixture",
          },
          {
            node_id: "project:fixture",
            kind: "project",
            label: "青禾项目",
            room_id: "room:fixture",
          },
        ],
        edges: [
          {
            relation_id: "edge:fixture",
            source: "person:fixture",
            target: "project:fixture",
            predicate: "responsible",
            description: "小林负责青禾项目设计稿",
            revision: 1,
            status: "proposed",
            valid_from: 1791800000000,
            valid_to: null,
            source_refs: item.source_refs,
          },
        ],
        revision: 1,
        policy_epoch: 0,
        truncated: false,
      };
    if (path === "/status")
      response = {
        records: 2,
        policy_epoch: 0,
        index: { done: 2 },
        devices: [],
      };
    if (path === "/records") response = { items: [item] };
    if (path.startsWith("/records/"))
      response = path.endsWith("work%3Afixture") ? item : source;
    if (path === "/conflicts") response = { items: [] };
    if (path.startsWith("/entities/"))
      response = {
        node: {
          node_id: "person:fixture",
          kind: "person",
          label: "小林",
          room_id: "room:fixture",
        },
        sources: item.source_refs,
      };
    if (path === "/commands") {
      const command = body as { mutations: { operation_id: string }[] };
      response = {
        receipts: [
          {
            status: conflict ? "conflict" : "accepted",
            operation_id: command.mutations[0].operation_id,
          },
        ],
      };
    }
    if (path.endsWith("/correct")) response = { updated: true };
    if (path.endsWith("/history")) response = { items: [] };
    if (path === "/query")
      response = {
        claims: [
          {
            text: "评审已经改到周五。",
            sources: ["source:fixture:v1"],
            tentative: true,
          },
        ],
        limitations: "这是待核实的消息约定。",
        degraded: false,
        sources: [{ ...source, key: "source:fixture:v1" }],
      };
    await route.fulfill({ json: response });
  });
  await page.goto(`/knowledge?embedded=1&session_id=${session}`);
  await expect(
    page.getByRole("button", { name: "刷新", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText("节点列表与合并（2）")).toBeVisible();
  return events;
}

test("map source drawer and mobile layout preserve evidence as text", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await fixture(page);
  await page.getByText("节点列表与合并（2）").click();
  await page.getByRole("button", { name: "小林", exact: true }).click();
  await page.getByRole("button", { name: "查看原文 · 版本 1" }).click();
  await expect(page.locator("pre")).toContainText("<script>");
  expect(await page.evaluate(() => "fixtureExecuted" in window)).toBe(false);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("knowledge-mobile-source.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭详情 ×" }).click();
  await page.screenshot({
    path: info.outputPath("knowledge-mobile-map.png"),
    fullPage: true,
  });
});

test("web edit sends the original revision and typed record, then shows phone synchronization state", async ({
  page,
}) => {
  const events = await fixture(page);
  await page.getByRole("button", { name: "记忆与事项", exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("标题", { exact: true }).fill("青禾项目周五评审");
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByRole("status")).toContainText("手机同步后");
  const command = events.find((e) => e.path === "/commands")?.body as {
    mutations: { expected_revision: number; record: { title: string } }[];
  };
  expect(command.mutations[0].expected_revision).toBe(1);
  expect(command.mutations[0].record.title).toBe("青禾项目周五评审");
});

test("HTTP success containing a conflict never reports saved", async ({
  page,
}) => {
  await fixture(page, true);
  await page.getByRole("button", { name: "记忆与事项", exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByRole("status")).toContainText("双方版本已保留");
  await expect(
    page.getByRole("heading", { name: "双方修改都已保留" }),
  ).toBeVisible();
});

test("question answer retains tentative status and exact citation", async ({
  page,
}, info) => {
  await fixture(page);
  await page.getByRole("button", { name: "问宁宁", exact: true }).click();
  await page.getByLabel("想了解什么").fill("青禾项目的评审什么时候？");
  await page.getByRole("button", { name: "检索并回答" }).click();
  await expect(page.getByText("含待核实信息")).toBeVisible();
  await page.getByRole("button", { name: "查看原文 · 版本 1" }).click();
  await expect(page.locator("pre")).toContainText("周四改到周五");
  await page.screenshot({
    path: info.outputPath("knowledge-desktop-answer.png"),
    fullPage: true,
  });
});

test("human relation correction has explicit confirmation and an expected version", async ({
  page,
}) => {
  const events = await fixture(page);
  await page.getByText("关系列表（1）").click();
  await page
    .getByRole("button", { name: "查看关系：小林负责青禾项目设计稿" })
    .click();
  await page.getByText("更正这条关系", { exact: true }).click();
  await page
    .getByLabel("更正内容", { exact: true })
    .fill("小林协助青禾项目设计稿");
  await page.getByRole("button", { name: "确认并保存更正" }).click();
  await expect(page.getByRole("status")).toContainText("关系已更正");
  const request = events.find((e) => e.path.endsWith("/correct"))?.body as {
    expected_revision: number;
    description: string;
  };
  expect(request.expected_revision).toBe(1);
  expect(request.description).toBe("小林协助青禾项目设计稿");
});
