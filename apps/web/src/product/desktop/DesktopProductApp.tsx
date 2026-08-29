import { DesktopPetPage } from "../../features/desktop-pet/DesktopPetPage";
import { DesktopSettingsPage } from "../../features/desktop-settings/DesktopSettingsPage";
import { resolveDesktopSurface, type DesktopSurface } from "./desktopSurface";

interface DesktopProductAppProps {
  surface?: DesktopSurface;
}

export function DesktopProductApp({
  surface = resolveDesktopSurface(),
}: DesktopProductAppProps) {
  return surface === "desktop-settings" ? (
    <DesktopSettingsPage />
  ) : (
    <DesktopPetPage />
  );
}
