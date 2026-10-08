export type WebSurface =
  | "application"
  | "avatar-lab"
  | "channels-settings"
  | "agent-settings"
  | "settings";

export function resolveWebSurface(pathname: string): WebSurface {
  if (pathname === "/settings/agent") return "agent-settings";
  if (pathname === "/settings/channels") return "channels-settings";
  if (
    pathname === "/settings" ||
    /^\/settings\/(models|voice|extensions|memory|personal-assistant|connection)$/.test(
      pathname,
    )
  )
    return "settings";
  return pathname === "/avatar-lab" ? "avatar-lab" : "application";
}
