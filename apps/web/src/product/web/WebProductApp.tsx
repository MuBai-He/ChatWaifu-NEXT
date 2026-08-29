import { AvatarLabPage } from "../../features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "../../features/chat/ChatDemoPage";

export type WebSurface = "application" | "avatar-lab";

export function resolveWebSurface(pathname: string): WebSurface {
  return pathname === "/avatar-lab" ? "avatar-lab" : "application";
}

interface WebProductAppProps {
  surface?: WebSurface;
}

export function WebProductApp({
  surface = resolveWebSurface(window.location.pathname),
}: WebProductAppProps) {
  return surface === "avatar-lab" ? <AvatarLabPage /> : <ChatDemoPage />;
}
