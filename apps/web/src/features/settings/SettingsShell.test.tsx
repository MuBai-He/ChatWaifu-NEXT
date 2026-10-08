import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsShell } from "./SettingsShell";
import { defineSettingsRegistry } from "./settingsRegistry";

const sections = defineSettingsRegistry<object>()([
  {
    id: "channels",
    label: "聊天与渠道",
    description: "QQ 和微信",
    icon: "channels",
    group: "聊天",
    keywords: ["延迟", "自由交流"],
    component: () => null,
  },
  {
    id: "models",
    label: "模型",
    description: "行为决策",
    icon: "models",
    group: "模型与声音",
    keywords: ["Jev"],
    component: () => null,
  },
  {
    id: "connection",
    label: "连接",
    description: "设备连接",
    icon: "models",
    group: "连接与设备",
    availability: () => ({ enabled: false, reason: "当前设备不支持" }),
    component: () => null,
  },
]);
afterEach(cleanup);

describe("shared settings navigation", () => {
  it("finds a setting by feature keyword, retains the current heading, and clears search on selection", () => {
    const select = vi.fn();
    render(
      <SettingsShell
        sections={sections}
        selectedId="channels"
        onSelect={select}
        context={{}}
        connection="connected"
        subtitle="设置中心"
      >
        <p>当前设置</p>
      </SettingsShell>,
    );
    fireEvent.change(screen.getByRole("searchbox", { name: "查找设置" }), {
      target: { value: "jev" },
    });
    const nav = screen.getByRole("navigation", { name: "设置分类" });
    expect(
      within(nav).queryByRole("button", { name: /聊天与渠道/ }),
    ).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "聊天与渠道",
    );
    fireEvent.click(within(nav).getByRole("button", { name: /模型/ }));
    expect(select).toHaveBeenCalledWith("models");
    expect(screen.getByRole<HTMLInputElement>("searchbox").value).toBe("");
    expect(
      within(nav).getByRole("button", { name: /聊天与渠道/ }),
    ).toBeTruthy();
  });

  it("shows the reason for an unavailable section and an empty search result", () => {
    render(
      <SettingsShell
        sections={sections}
        selectedId="channels"
        onSelect={vi.fn()}
        context={{}}
        connection="offline"
        subtitle="设置中心"
      >
        内容
      </SettingsShell>,
    );
    expect(
      screen.getByRole<HTMLButtonElement>("button", {
        name: /当前设备不支持/,
      }).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "不存在" },
    });
    expect(screen.getByText(/没有匹配的设置/)).toBeTruthy();
  });
});
