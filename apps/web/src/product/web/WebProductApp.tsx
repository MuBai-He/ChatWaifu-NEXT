import { AvatarLabPage } from "../../features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "../../features/chat/ChatDemoPage";
import { resolveWebSurface, type WebSurface } from "./webSurface";

interface WebProductAppProps {
  surface?: WebSurface;
}

export function WebProductApp({
  surface = resolveWebSurface(window.location.pathname),
}: WebProductAppProps) {
  return surface === "avatar-lab" ? <AvatarLabPage /> : <ChatDemoPage />;
}
