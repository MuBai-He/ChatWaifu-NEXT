export type WebSurface = "application" | "avatar-lab" | "channels-settings";

export function resolveWebSurface(pathname: string): WebSurface {
  if (pathname === "/settings/channels") return "channels-settings";
  return pathname === "/avatar-lab" ? "avatar-lab" : "application";
}
