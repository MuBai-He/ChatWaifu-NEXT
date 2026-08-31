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

interface ClientSize {
  width: number;
  height: number;
}

interface DesktopPointerPresenceOptions {
  isInteractiveAtPoint?: (clientX: number, clientY: number) => boolean;
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

export function mapPhysicalPointToClient(
  point: PhysicalPoint,
  windowOrigin: PhysicalPoint,
  windowSize: PhysicalSize,
  viewportSize: ClientSize,
): PhysicalPoint | null {
  if (
    !isPointInsideWindow(point, windowOrigin, windowSize) ||
    windowSize.width <= 0 ||
    windowSize.height <= 0 ||
    viewportSize.width <= 0 ||
    viewportSize.height <= 0
  ) {
    return null;
  }
  return {
    x: (point.x - windowOrigin.x) * (viewportSize.width / windowSize.width),
    y: (point.y - windowOrigin.y) * (viewportSize.height / windowSize.height),
  };
}

export function useDesktopPointerPresence({
  isInteractiveAtPoint = () => false,
}: DesktopPointerPresenceOptions = {}) {
  const [pointerInside, setPointerInside] = useState(false);
  const pointerInsideRef = useRef(false);
  const physicalPointerInsideRef = useRef(false);
  const physicalPointerInteractiveRef = useRef(false);
  const interactionGuardRef = useRef(false);
  const interactiveProbeRef = useRef(isInteractiveAtPoint);
  const nativeInteractionRef = useRef(false);
  const nativePresenceSyncRef = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    interactiveProbeRef.current = isInteractiveAtPoint;
  }, [isInteractiveAtPoint]);

  const syncNativeInteraction = useCallback((active: boolean) => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    nativePresenceSyncRef.current = nativePresenceSyncRef.current
      .catch(() => undefined)
      .then(async () => {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("set_avatar_overlay_interaction_region_active", {
          active,
        });
      })
      .catch(() => undefined);
  }, []);

  const updateNativeInteraction = useCallback(
    (active: boolean) => {
      if (nativeInteractionRef.current === active) return;
      nativeInteractionRef.current = active;
      syncNativeInteraction(active);
    },
    [syncNativeInteraction],
  );

  const updatePointerInside = useCallback((inside: boolean) => {
    if (pointerInsideRef.current === inside) return;
    pointerInsideRef.current = inside;
    setPointerInside(inside);
  }, []);

  const updatePhysicalPointer = useCallback(
    (point: PhysicalPoint | null) => {
      const inside = point !== null;
      let interactive = false;
      if (point) {
        try {
          interactive = interactiveProbeRef.current(point.x, point.y);
        } catch {
          // Renderer teardown must fail closed so transparent pixels stay pass-through.
        }
      }
      physicalPointerInsideRef.current = inside;
      physicalPointerInteractiveRef.current = interactive;
      updatePointerInside(
        shouldKeepDesktopInteraction(inside, interactionGuardRef.current),
      );
      updateNativeInteraction(
        shouldCaptureDesktopInteraction(
          inside,
          interactive,
          interactionGuardRef.current,
        ),
      );
    },
    [updateNativeInteraction, updatePointerInside],
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
      updateNativeInteraction(
        shouldCaptureDesktopInteraction(
          physicalPointerInsideRef.current,
          physicalPointerInteractiveRef.current,
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
    updateNativeInteraction(
      shouldCaptureDesktopInteraction(
        physicalPointerInsideRef.current,
        physicalPointerInteractiveRef.current,
        active,
      ),
    );
    return () =>
      window.removeEventListener(
        NATIVE_INTERACTION_GUARD_NOTIFICATION,
        updateGuard,
      );
  }, [updateNativeInteraction, updatePointerInside]);

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
            updatePhysicalPointer(
              mapPhysicalPointToClient(pointer, origin, size, {
                width: window.innerWidth,
                height: window.innerHeight,
              }),
            );
          } catch {
            consecutiveErrors += 1;
            if (
              consecutiveErrors >= MAX_CONSECUTIVE_SAMPLE_ERRORS &&
              intervalId !== null
            ) {
              window.clearInterval(intervalId);
              intervalId = null;
              updatePhysicalPointer(null);
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
  }, [updatePhysicalPointer]);

  useEffect(
    () => () => {
      if (nativeInteractionRef.current) syncNativeInteraction(false);
    },
    [syncNativeInteraction],
  );

  return {
    pointerInside,
    onPointerEnter: (event: { clientX: number; clientY: number }) =>
      updatePhysicalPointer({ x: event.clientX, y: event.clientY }),
    onPointerMove: (event: { clientX: number; clientY: number }) =>
      updatePhysicalPointer({ x: event.clientX, y: event.clientY }),
    onPointerLeave: () => updatePhysicalPointer(null),
  };
}

export function shouldKeepDesktopInteraction(
  pointerInside: boolean,
  interactionGuardActive: boolean,
): boolean {
  return pointerInside || interactionGuardActive;
}

export function shouldCaptureDesktopInteraction(
  pointerInside: boolean,
  pointInteractive: boolean,
  interactionGuardActive: boolean,
): boolean {
  return interactionGuardActive || (pointerInside && pointInteractive);
}
