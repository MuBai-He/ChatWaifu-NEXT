export type WebSurface =
  | "application"
  | "avatar-lab"
  | "channels-settings"
  | "agent-settings"
  | "knowledge"
  | "settings";

export function resolveWebSurface(pathname: string): WebSurface {
  if (pathname === "/knowledge") return "knowledge";
  if (pathname === "/settings/agent") return "agent-settings";
  if (pathname === "/settings/channels") return "channels-settings";
  if (
    pathname === "/settings" ||
    /^\/settings\/(models|voice|extensions|memory|knowledge|personal-assistant|connection)$/.test(
      pathname,
    )
  )
    return "settings";
  return pathname === "/avatar-lab" ? "avatar-lab" : "application";
}
