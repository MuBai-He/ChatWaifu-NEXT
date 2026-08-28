import "./App.css";
import { resolveAppSurface } from "./appSurface";
import { AvatarLabPage } from "./features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "./features/chat/ChatDemoPage";
import { DesktopPetPage } from "./features/desktop-pet/DesktopPetPage";
import { DesktopSettingsPage } from "./features/desktop-settings/DesktopSettingsPage";

export default function App() {
  const surface = resolveAppSurface();
  if (surface === "avatar-lab") {
    return <AvatarLabPage />;
  }
  if (surface === "desktop-pet") {
    return <DesktopPetPage />;
  }
  if (surface === "desktop-settings") {
    return <DesktopSettingsPage />;
  }
  return <ChatDemoPage />;
}
