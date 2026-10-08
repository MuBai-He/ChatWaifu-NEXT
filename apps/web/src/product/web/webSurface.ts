export type WebSurface =
  "application" | "avatar-lab" | "channels-settings" | "agent-settings";

export function resolveWebSurface(pathname: string): WebSurface {
  if (pathname === "/settings/agent") return "agent-settings";
  if (pathname === "/settings/channels") return "channels-settings";
  return pathname === "/avatar-lab" ? "avatar-lab" : "application";
}
