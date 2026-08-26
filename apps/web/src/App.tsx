import "./App.css";
import { AvatarLabPage } from "./features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "./features/chat/ChatDemoPage";
import { DesktopPetPage } from "./features/desktop-pet/DesktopPetPage";
import { DesktopSettingsPage } from "./features/desktop-settings/DesktopSettingsPage";

export default function App() {
  if (window.location.pathname === "/avatar-lab") {
    return <AvatarLabPage />;
  }
  if (window.location.pathname === "/desktop-pet") {
    return <DesktopPetPage />;
  }
  if (
    window.location.pathname === "/desktop-settings" ||
    window.location.pathname === "/control-center"
  ) {
    return <DesktopSettingsPage />;
  }
  return <ChatDemoPage />;
}
