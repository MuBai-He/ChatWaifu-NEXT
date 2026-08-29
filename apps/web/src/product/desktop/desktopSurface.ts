import { getCurrentWindow } from "@tauri-apps/api/window";

export type DesktopSurface = "desktop-pet" | "desktop-settings";

declare global {
  interface Window {
    __CHATWAIFU_NATIVE_SURFACE__?: DesktopSurface;
  }
}

const NATIVE_SURFACE_QUERY = "chatwaifu_surface";

export function resolveDesktopSurface(
  pathname = window.location.pathname,
): DesktopSurface {
  return resolveDesktopSurfaceFromContext(
    pathname,
    currentNativeWindowLabel(),
    normalizeDesktopSurface(window.__CHATWAIFU_NATIVE_SURFACE__),
    normalizeDesktopSurface(
      new URLSearchParams(window.location.search).get(NATIVE_SURFACE_QUERY),
    ),
  );
}

export function resolveDesktopSurfaceFromContext(
  pathname: string,
  nativeWindowLabel: string | null,
  injectedSurface: DesktopSurface | null = null,
  querySurface: DesktopSurface | null = null,
): DesktopSurface {
  if (injectedSurface) return injectedSurface;
  if (querySurface) return querySurface;
  if (nativeWindowLabel === "control-center") return "desktop-settings";
  if (nativeWindowLabel === "avatar-overlay") return "desktop-pet";
  if (pathname === "/desktop-settings" || pathname === "/control-center") {
    return "desktop-settings";
  }
  return "desktop-pet";
}

function normalizeDesktopSurface(value: unknown): DesktopSurface | null {
  return value === "desktop-pet" || value === "desktop-settings" ? value : null;
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
