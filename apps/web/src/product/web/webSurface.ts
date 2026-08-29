export type WebSurface = "application" | "avatar-lab";

export function resolveWebSurface(pathname: string): WebSurface {
  return pathname === "/avatar-lab" ? "avatar-lab" : "application";
}
