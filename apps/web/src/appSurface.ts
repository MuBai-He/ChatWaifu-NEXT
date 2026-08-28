import { getCurrentWindow } from "@tauri-apps/api/window";

export type AppSurface =
  "application" | "avatar-lab" | "desktop-pet" | "desktop-settings";

type NativeAppSurface = Extract<
  AppSurface,
  "desktop-pet" | "desktop-settings"
>;

declare global {
  interface Window {
    __CHATWAIFU_NATIVE_SURFACE__?: NativeAppSurface;
  }
}

const NATIVE_SURFACE_QUERY = "chatwaifu_surface";

export function resolveAppSurface(
  pathname = window.location.pathname,
): AppSurface {
  return resolveAppSurfaceFromContext(
    pathname,
    currentNativeWindowLabel(),
    currentInjectedNativeSurface(),
    currentQueryNativeSurface(),
  );
}

export function resolveAppSurfaceFromContext(
  pathname: string,
  nativeWindowLabel: string | null,
  injectedNativeSurface: NativeAppSurface | null = null,
  queryNativeSurface: NativeAppSurface | null = null,
): AppSurface {
  if (injectedNativeSurface) return injectedNativeSurface;
  if (queryNativeSurface) return queryNativeSurface;
  if (nativeWindowLabel === "control-center") return "desktop-settings";
  if (nativeWindowLabel === "avatar-overlay") return "desktop-pet";
  if (pathname === "/avatar-lab") return "avatar-lab";
  if (pathname === "/desktop-pet") return "desktop-pet";
  if (pathname === "/desktop-settings" || pathname === "/control-center") {
    return "desktop-settings";
  }
  return "application";
}

function currentInjectedNativeSurface(): NativeAppSurface | null {
  return normalizeNativeSurface(window.__CHATWAIFU_NATIVE_SURFACE__);
}

function currentQueryNativeSurface(): NativeAppSurface | null {
  return normalizeNativeSurface(
    new URLSearchParams(window.location.search).get(NATIVE_SURFACE_QUERY),
  );
}

function normalizeNativeSurface(value: unknown): NativeAppSurface | null {
  return value === "desktop-pet" || value === "desktop-settings"
    ? value
    : null;
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
