import type { AvatarInteractionEvent } from "@chatwaifu/protocol";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  useCallback,
  useEffect,
  useRef,
  type PointerEventHandler,
} from "react";
import { acquireNativeInteractionGuard } from "../../nativeInteractionGuard";

const dragThresholdPx = 6;

type AvatarGesture = {
  pointerId: number;
  startX: number;
  startY: number;
  hitAvatar: boolean;
  dragging: boolean;
  releaseInteractionGuard: (() => void) | null;
};

type DesktopAvatarDragOptions = {
  hitTest: (clientX: number, clientY: number) => AvatarInteractionEvent[];
  touch: () => void;
  onError: (message: string) => void;
};

export function useDesktopAvatarDrag({
  hitTest,
  touch,
  onError,
}: DesktopAvatarDragOptions) {
  const gestureRef = useRef<AvatarGesture | null>(null);
  const desktopHost = "__TAURI_INTERNALS__" in window;

  const onPointerDown = useCallback<PointerEventHandler<HTMLButtonElement>>(
    (event) => {
      if (event.button !== 0) return;
      const hits = hitTest(event.clientX, event.clientY);
      if (gestureRef.current) {
        releaseGestureInteractionGuard(gestureRef.current);
      }
      gestureRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        hitAvatar: hits.length > 0,
        dragging: false,
        releaseInteractionGuard:
          hits.length > 0
            ? acquireNativeInteractionGuard("avatar-gesture")
            : null,
      };
      event.currentTarget.setPointerCapture?.(event.pointerId);
    },
    [hitTest],
  );

  const onPointerMove = useCallback<PointerEventHandler<HTMLButtonElement>>(
    (event) => {
      const gesture = gestureRef.current;
      if (
        !desktopHost ||
        !gesture ||
        gesture.pointerId !== event.pointerId ||
        !gesture.hitAvatar ||
        gesture.dragging ||
        Math.hypot(
          event.clientX - gesture.startX,
          event.clientY - gesture.startY,
        ) < dragThresholdPx
      ) {
        return;
      }
      gesture.dragging = true;
      event.preventDefault();
      releasePointerCapture(event.currentTarget, event.pointerId);
      try {
        void getCurrentWindow()
          .startDragging()
          .catch((dragError: unknown) => reportDragError(dragError, onError))
          .finally(() => releaseGestureInteractionGuard(gesture));
      } catch (dragError: unknown) {
        releaseGestureInteractionGuard(gesture);
        reportDragError(dragError, onError);
      }
    },
    [desktopHost, onError],
  );

  const finishGesture = useCallback<PointerEventHandler<HTMLButtonElement>>(
    (event) => {
      const gesture = gestureRef.current;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      gestureRef.current = null;
      releaseGestureInteractionGuard(gesture);
      releasePointerCapture(event.currentTarget, event.pointerId);
      if (gesture.hitAvatar && !gesture.dragging) touch();
    },
    [touch],
  );

  const cancelGesture = useCallback<PointerEventHandler<HTMLButtonElement>>(
    (event) => {
      if (gestureRef.current?.pointerId === event.pointerId) {
        releaseGestureInteractionGuard(gestureRef.current);
        gestureRef.current = null;
      }
      releasePointerCapture(event.currentTarget, event.pointerId);
    },
    [],
  );

  useEffect(
    () => () => {
      if (gestureRef.current) {
        releaseGestureInteractionGuard(gestureRef.current);
      }
      gestureRef.current = null;
    },
    [],
  );

  return {
    onPointerDown,
    onPointerMove,
    onPointerUp: finishGesture,
    onPointerCancel: cancelGesture,
  };
}

function releaseGestureInteractionGuard(gesture: AvatarGesture) {
  gesture.releaseInteractionGuard?.();
  gesture.releaseInteractionGuard = null;
}

function releasePointerCapture(element: HTMLButtonElement, pointerId: number) {
  if (element.hasPointerCapture?.(pointerId)) {
    element.releasePointerCapture(pointerId);
  }
}

function reportDragError(
  dragError: unknown,
  onError: (message: string) => void,
) {
  onError(
    dragError instanceof Error ? dragError.message : "无法通过角色移动桌宠",
  );
}
