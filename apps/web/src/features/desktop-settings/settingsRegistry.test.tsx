import { describe, expect, it } from "vitest";

import {
  defineSettingsRegistry,
  settingsSectionAvailability,
  visibleSettingsSections,
} from "./settingsRegistry";

type Context = { featureEnabled: boolean };

function EmptySection() {
  return null;
}

describe("settings registry", () => {
  it("is the single source for ordering, platform filtering, and visibility", () => {
    const registry = defineSettingsRegistry<Context>()([
      {
        id: "common",
        label: "通用",
        description: "所有平台",
        icon: "pet",
        component: EmptySection,
      },
      {
        id: "desktop-only",
        label: "桌面",
        description: "仅桌面",
        icon: "companion",
        component: EmptySection,
        surfaces: ["desktop"],
      },
      {
        id: "conditional",
        label: "条件",
        description: "能力控制",
        icon: "voice",
        component: EmptySection,
        visible: (context) => context.featureEnabled,
      },
    ]);

    expect(
      visibleSettingsSections(registry, { featureEnabled: false }, "browser").map(
        (item) => item.id,
      ),
    ).toEqual(["common"]);
    expect(
      visibleSettingsSections(registry, { featureEnabled: true }, "desktop").map(
        (item) => item.id,
      ),
    ).toEqual(["common", "desktop-only", "conditional"]);
  });

  it("normalizes availability and rejects duplicate ids", () => {
    const registry = defineSettingsRegistry<Context>()([
      {
        id: "runtime",
        label: "Runtime",
        description: "需要服务",
        icon: "models",
        component: EmptySection,
        availability: () => ({ enabled: false, reason: "Runtime 离线" }),
      },
    ]);
    expect(
      settingsSectionAvailability(registry[0], { featureEnabled: true }),
    ).toEqual({ enabled: false, reason: "Runtime 离线" });

    expect(() =>
      defineSettingsRegistry<Context>()([
        {
          id: "same",
          label: "一",
          description: "一",
          icon: "pet",
          component: EmptySection,
        },
        {
          id: "same",
          label: "二",
          description: "二",
          icon: "data",
          component: EmptySection,
        },
      ]),
    ).toThrow("Duplicate settings section id: same");
  });
});
