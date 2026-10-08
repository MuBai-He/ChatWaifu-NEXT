import type { useDesktopPreferences } from "../desktop-pet/useDesktopPreferences";
import type { CommonSettingsContext } from "../settings/SettingsRuntimeContext";

export interface SettingsRuntimeContext extends CommonSettingsContext {
  desktop: ReturnType<typeof useDesktopPreferences>;
}

export type DesktopSettingsContext = SettingsRuntimeContext;
