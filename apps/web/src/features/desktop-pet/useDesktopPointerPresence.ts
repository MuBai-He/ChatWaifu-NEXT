import { useCallback, useEffect, useRef, useState } from "react";
import {
  isNativeInteractionGuardActive,
  NATIVE_INTERACTION_GUARD_NOTIFICATION,
  type NativeInteractionGuardNotification,
} from "../../nativeInteractionGuard";

const POINTER_SAMPLE_INTERVAL_MS = 80;
const MAX_CONSECUTIVE_SAMPLE_ERRORS = 3;

interface PhysicalPoint {
  x: number;
  y: number;
}

interface PhysicalSize {
  width: number;
  height: number;
}

export function isPointInsideWindow(
  point: PhysicalPoint,
  windowOrigin: PhysicalPoint,
  windowSize: PhysicalSize,
): boolean {
  return (
    point.x >= windowOrigin.x &&
    point.y >= windowOrigin.y &&
    point.x < windowOrigin.x + windowSize.width &&
    point.y < windowOrigin.y + windowSize.height
  );
}

export function useDesktopPointerPresence() {
  const [pointerInside, setPointerInside] = useState(false);
  const pointerInsideRef = useRef(false);
  const physicalPointerInsideRef = useRef(false);
  const interactionGuardRef = useRef(false);
  const nativePresenceSyncRef = useRef<Promise<void>>(Promise.resolve());

  const syncNativePresence = useCallback((inside: boolean) => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    nativePresenceSyncRef.current = nativePresenceSyncRef.current
      .catch(() => undefined)
      .then(async () => {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("set_avatar_overlay_pointer_inside", { inside });
      })
      .catch(() => undefined);
  }, []);

  const updatePointerInside = useCallback(
    (inside: boolean) => {
      if (pointerInsideRef.current === inside) return;
      pointerInsideRef.current = inside;
      syncNativePresence(inside);
      setPointerInside(inside);
    },
    [syncNativePresence],
  );

  const updatePhysicalPointerInside = useCallback(
    (inside: boolean) => {
      physicalPointerInsideRef.current = inside;
      updatePointerInside(
        shouldKeepDesktopInteraction(inside, interactionGuardRef.current),
      );
    },
    [updatePointerInside],
  );

  useEffect(() => {
    const updateGuard = (rawEvent: Event) => {
      const event = rawEvent as CustomEvent<NativeInteractionGuardNotification>;
      interactionGuardRef.current = event.detail.active;
      updatePointerInside(
        shouldKeepDesktopInteraction(
          physicalPointerInsideRef.current,
          event.detail.active,
        ),
      );
    };
    window.addEventListener(NATIVE_INTERACTION_GUARD_NOTIFICATION, updateGuard);
    const active = isNativeInteractionGuardActive();
    interactionGuardRef.current = active;
    updatePointerInside(
      shouldKeepDesktopInteraction(physicalPointerInsideRef.current, active),
    );
    return () =>
      window.removeEventListener(
        NATIVE_INTERACTION_GUARD_NOTIFICATION,
        updateGuard,
      );
  }, [updatePointerInside]);

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;

    let cancelled = false;
    let sampleInFlight = false;
    let consecutiveErrors = 0;
    let intervalId: number | null = null;

    void import("@tauri-apps/api/window")
      .then(({ cursorPosition, getCurrentWindow }) => {
        if (cancelled) return;
        const currentWindow = getCurrentWindow();
        const samplePointer = async () => {
          if (cancelled || sampleInFlight) return;
          sampleInFlight = true;
          try {
            const [pointer, origin, size] = await Promise.all([
              cursorPosition(),
              currentWindow.outerPosition(),
              currentWindow.innerSize(),
            ]);
            if (cancelled) return;
            consecutiveErrors = 0;
            updatePhysicalPointerInside(
              isPointInsideWindow(pointer, origin, size),
            );
          } catch {
            consecutiveErrors += 1;
            if (
              consecutiveErrors >= MAX_CONSECUTIVE_SAMPLE_ERRORS &&
              intervalId !== null
            ) {
              window.clearInterval(intervalId);
              intervalId = null;
              updatePhysicalPointerInside(false);
            }
          } finally {
            sampleInFlight = false;
          }
        };

        void samplePointer();
        intervalId = window.setInterval(
          () => void samplePointer(),
          POINTER_SAMPLE_INTERVAL_MS,
        );
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
      if (intervalId !== null) window.clearInterval(intervalId);
    };
  }, [updatePhysicalPointerInside]);

  useEffect(
    () => () => {
      if (pointerInsideRef.current) syncNativePresence(false);
    },
    [syncNativePresence],
  );

  return {
    pointerInside,
    onPointerEnter: () => updatePhysicalPointerInside(true),
    onPointerLeave: () => updatePhysicalPointerInside(false),
  };
}

export function shouldKeepDesktopInteraction(
  pointerInside: boolean,
  interactionGuardActive: boolean,
): boolean {
  return pointerInside || interactionGuardActive;
}
