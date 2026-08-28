import { getCurrentWindow } from "@tauri-apps/api/window";

export type AppSurface =
  "application" | "avatar-lab" | "desktop-pet" | "desktop-settings";

export function resolveAppSurface(
  pathname = window.location.pathname,
): AppSurface {
  return resolveAppSurfaceFromContext(pathname, currentNativeWindowLabel());
}

export function resolveAppSurfaceFromContext(
  pathname: string,
  nativeWindowLabel: string | null,
): AppSurface {
  if (nativeWindowLabel === "control-center") return "desktop-settings";
  if (nativeWindowLabel === "avatar-overlay") return "desktop-pet";
  if (pathname === "/avatar-lab") return "avatar-lab";
  if (pathname === "/desktop-pet") return "desktop-pet";
  if (pathname === "/desktop-settings" || pathname === "/control-center") {
    return "desktop-settings";
  }
  return "application";
}

function currentNativeWindowLabel(): string | null {
  if (!("__TAURI_INTERNALS__" in window)) return null;
  try {
    return getCurrentWindow().label;
  } catch {
    // Browser previews and incomplete test hosts keep pathname routing.
    return null;
  }
}
