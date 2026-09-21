import { ConversationScopeGate } from "../../features/chat/ConversationScopeGate";
import { DesktopPetPage } from "../../features/desktop-pet/DesktopPetPage";
import { DesktopSettingsPage } from "../../features/desktop-settings/DesktopSettingsPage";
import { resolveDesktopSurface, type DesktopSurface } from "./desktopSurface";

interface DesktopProductAppProps {
  surface?: DesktopSurface;
}

export function DesktopProductApp({
  surface = resolveDesktopSurface(),
}: DesktopProductAppProps) {
  return (
    <ConversationScopeGate>
      {surface === "desktop-settings" ? (
        <DesktopSettingsPage />
      ) : (
        <DesktopPetPage />
      )}
    </ConversationScopeGate>
  );
}
