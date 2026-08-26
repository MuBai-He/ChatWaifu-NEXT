import "./App.css";
import { AvatarLabPage } from "./features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "./features/chat/ChatDemoPage";
import { DesktopPetPage } from "./features/desktop-pet/DesktopPetPage";

export default function App() {
  if (window.location.pathname === "/avatar-lab") {
    return <AvatarLabPage />;
  }
  if (window.location.pathname === "/desktop-pet") {
    return <DesktopPetPage />;
  }
  if (window.location.pathname === "/control-center") {
    return <ChatDemoPage mediaOwner={false} />;
  }
  return <ChatDemoPage />;
}
