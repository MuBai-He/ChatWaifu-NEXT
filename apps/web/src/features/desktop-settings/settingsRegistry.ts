import type { ComponentType } from "react";

import type { SettingsIconName } from "./SettingsIcon";

export type SettingsSurface = "browser" | "desktop";

export interface SettingsSectionAvailability {
  enabled: boolean;
  reason?: string;
}

export interface SettingsSectionDefinition<
  Context,
  Id extends string = string,
> {
  id: Id;
  label: string;
  description: string;
  icon: SettingsIconName;
  component: ComponentType<{ context: Context }>;
  surfaces?: readonly SettingsSurface[];
  visible?: (context: Context) => boolean;
  availability?: (context: Context) => SettingsSectionAvailability;
}

export function defineSettingsRegistry<Context>() {
  return <
    const Definitions extends readonly SettingsSectionDefinition<Context>[],
  >(
    definitions: Definitions,
  ): Definitions => {
    const ids = new Set<string>();
    for (const definition of definitions) {
      if (ids.has(definition.id))
        throw new Error(`Duplicate settings section id: ${definition.id}`);
      ids.add(definition.id);
    }
    if (!definitions.length)
      throw new Error("Settings registry must contain at least one section");
    return definitions;
  };
}

export function visibleSettingsSections<Context>(
  registry: readonly SettingsSectionDefinition<Context>[],
  context: Context,
  surface: SettingsSurface,
): SettingsSectionDefinition<Context>[] {
  return registry.filter(
    (section) =>
      (!section.surfaces || section.surfaces.includes(surface)) &&
      (section.visible?.(context) ?? true),
  );
}

export function settingsSectionAvailability<Context>(
  section: SettingsSectionDefinition<Context>,
  context: Context,
): SettingsSectionAvailability {
  return section.availability?.(context) ?? { enabled: true };
}
