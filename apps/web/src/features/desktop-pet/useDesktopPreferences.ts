import { useCallback, useEffect, useRef, useState } from "react";

export type DesktopPreferences = {
  alwaysOnTop: boolean;
  clickThrough: boolean;
  overlayVisible: boolean;
  showSubtitles: boolean;
  showStatus: boolean;
  overlayWidth: number | null;
  overlayHeight: number | null;
};

type DesktopHostPreferences = {
  always_on_top?: boolean;
  click_through?: boolean;
  overlay_visible?: boolean;
  show_subtitles?: boolean;
  show_status?: boolean;
  overlay_width?: number | null;
  overlay_height?: number | null;
};

const defaultPreferences: DesktopPreferences = {
  alwaysOnTop: true,
  clickThrough: false,
  overlayVisible: true,
  showSubtitles: true,
  showStatus: true,
  overlayWidth: null,
  overlayHeight: null,
};

const browserPreferenceKey = "chatwaifu.desktop-pet.preferences.v1";
const legacyDisplayPreferenceKey = "chatwaifu.desktop-pet.display.v1";
const preferenceEvent = "desktop-preferences-changed";

export function useDesktopPreferences() {
  const [preferences, setPreferences] = useState(defaultPreferences);
  const preferencesRef = useRef(preferences);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const desktopHost = "__TAURI_INTERNALS__" in window;

  const apply = useCallback((next: DesktopPreferences) => {
    preferencesRef.current = next;
    setPreferences(next);
  }, []);

  useEffect(() => {
    let active = true;
    let stopListening: (() => void) | undefined;

    const restore = async () => {
      try {
        if (desktopHost) {
          const [{ invoke }, { listen }] = await Promise.all([
            import("@tauri-apps/api/core"),
            import("@tauri-apps/api/event"),
          ]);
          const unlisten = await listen<DesktopHostPreferences>(
            preferenceEvent,
            (event) => {
              if (active) apply(fromHostPreferences(event.payload));
            },
          );
          if (!active) {
            unlisten();
            return;
          }
          stopListening = unlisten;
          const stored = await invoke<DesktopHostPreferences>(
            "get_desktop_preferences",
          );
          if (active) apply(fromHostPreferences(stored));
          return;
        }

        const stored = readBrowserPreferences();
        if (active) apply(stored);
      } catch (restoreError: unknown) {
        if (active) setError(errorMessage(restoreError, "无法读取桌宠设置"));
      } finally {
        if (active) setLoading(false);
      }
    };

    const onStorage = (event: StorageEvent) => {
      if (event.key === browserPreferenceKey && event.newValue) {
        try {
          apply(fromBrowserValue(JSON.parse(event.newValue) as unknown));
        } catch {
          // A malformed browser-preview value must not break the pet surface.
        }
      }
    };
    if (!desktopHost) window.addEventListener("storage", onStorage);
    void restore();
    return () => {
      active = false;
      stopListening?.();
      window.removeEventListener("storage", onStorage);
    };
  }, [apply, desktopHost]);

  const commit = useCallback(
    async (
      next: DesktopPreferences,
      command: string,
      arguments_: Record<string, unknown>,
    ) => {
      const previous = preferencesRef.current;
      apply(next);
      setSaving(true);
      setError(null);
      if (!desktopHost) {
        try {
          window.localStorage.setItem(
            browserPreferenceKey,
            JSON.stringify(next),
          );
        } catch {
          // Private or test browsers may block storage; keep the in-memory preview usable.
        } finally {
          setSaving(false);
        }
        return;
      }
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        const stored = await invoke<DesktopHostPreferences>(
          command,
          arguments_,
        );
        apply(fromHostPreferences(stored));
      } catch (updateError: unknown) {
        apply(previous);
        setError(errorMessage(updateError, "无法保存桌宠设置"));
      } finally {
        setSaving(false);
      }
    },
    [apply, desktopHost],
  );

  const setDisplay = useCallback(
    (display: { showSubtitles?: boolean; showStatus?: boolean }) => {
      const next = { ...preferencesRef.current, ...display };
      return commit(next, "set_avatar_overlay_display", {
        showSubtitles: next.showSubtitles,
        showStatus: next.showStatus,
      });
    },
    [commit],
  );

  const setAlwaysOnTop = useCallback(
    (enabled: boolean) =>
      commit(
        { ...preferencesRef.current, alwaysOnTop: enabled },
        "set_avatar_overlay_always_on_top",
        { enabled },
      ),
    [commit],
  );

  const setClickThrough = useCallback(
    (enabled: boolean) =>
      commit(
        { ...preferencesRef.current, clickThrough: enabled },
        "set_avatar_overlay_click_through",
        { enabled },
      ),
    [commit],
  );

  const setOverlayVisible = useCallback(
    (enabled: boolean) =>
      commit(
        { ...preferencesRef.current, overlayVisible: enabled },
        "set_avatar_overlay_visible",
        { enabled },
      ),
    [commit],
  );

  return {
    preferences,
    loading,
    saving,
    error,
    desktopHost,
    setDisplay,
    setAlwaysOnTop,
    setClickThrough,
    setOverlayVisible,
  };
}

function fromHostPreferences(
  value: DesktopHostPreferences,
): DesktopPreferences {
  return {
    alwaysOnTop: value.always_on_top ?? true,
    clickThrough: value.click_through ?? false,
    overlayVisible: value.overlay_visible ?? true,
    showSubtitles: value.show_subtitles ?? true,
    showStatus: value.show_status ?? true,
    overlayWidth: value.overlay_width ?? null,
    overlayHeight: value.overlay_height ?? null,
  };
}

function readBrowserPreferences(): DesktopPreferences {
  try {
    const stored = window.localStorage.getItem(browserPreferenceKey);
    if (stored) return fromBrowserValue(JSON.parse(stored) as unknown);
    const legacy = window.localStorage.getItem(legacyDisplayPreferenceKey);
    if (legacy) return fromBrowserValue(JSON.parse(legacy) as unknown);
  } catch {
    // Browser preview storage is optional.
  }
  return defaultPreferences;
}

function fromBrowserValue(value: unknown): DesktopPreferences {
  if (typeof value !== "object" || value === null) return defaultPreferences;
  const stored = value as Partial<DesktopPreferences>;
  return {
    alwaysOnTop: booleanOr(stored.alwaysOnTop, true),
    clickThrough: booleanOr(stored.clickThrough, false),
    overlayVisible: booleanOr(stored.overlayVisible, true),
    showSubtitles: booleanOr(stored.showSubtitles, true),
    showStatus: booleanOr(stored.showStatus, true),
    overlayWidth: numberOrNull(stored.overlayWidth),
    overlayHeight: numberOrNull(stored.overlayHeight),
  };
}

function booleanOr(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
