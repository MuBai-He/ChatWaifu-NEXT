import "./App.css";
import { AvatarLabPage } from "./features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "./features/chat/ChatDemoPage";

export default function App() {
  if (window.location.pathname === "/avatar-lab") {
    return <AvatarLabPage />;
  }
  return <ChatDemoPage />;
}
