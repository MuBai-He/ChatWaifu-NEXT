import {
  AVATAR_LAB_MANIFEST,
  LIVE2D_LAB_MANIFEST,
  AvatarController,
  FakeAvatarRenderer,
  Live2DAvatarRenderer,
  SilentLipSyncSource,
  SyntheticLipSyncSource,
  type AvatarControllerSnapshot,
} from "@chatwaifu/avatar-sdk";
import type { AvatarCue, AvatarInteractionEvent } from "@chatwaifu/protocol";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAvatarCanvasLayoutSize,
  getAvatarCanvasRenderPixelRatio,
  mapClientPointToAvatarCanvas,
} from "./avatarCanvasGeometry";

export function useChatAvatar() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<AvatarController | null>(null);
  const [snapshot, setSnapshot] = useState<AvatarControllerSnapshot | null>(
    null,
  );
  const [rendererKind, setRendererKind] = useState<"live2d" | "fake">("live2d");
  const [avatarWarning, setAvatarWarning] = useState<string | null>(null);
  const avatarManifest =
    rendererKind === "live2d" ? LIVE2D_LAB_MANIFEST : AVATAR_LAB_MANIFEST;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let disposed = false;
    setSnapshot(null);
    const renderer =
      rendererKind === "live2d"
        ? new Live2DAvatarRenderer(canvas)
        : new FakeAvatarRenderer(canvas);
    const controller = new AvatarController(renderer, avatarManifest);
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);

    void controller.load().then(
      () => {
        if (!disposed && rendererKind === "live2d") setAvatarWarning(null);
      },
      (error: unknown) => {
        if (disposed) return;
        if (rendererKind === "live2d") {
          setAvatarWarning(
            error instanceof Error
              ? error.message
              : "Live2D unavailable; using the deterministic fallback.",
          );
          setRendererKind("fake");
          return;
        }
        setAvatarWarning(
          error instanceof Error
            ? error.message
            : "The fallback avatar renderer could not be loaded.",
        );
      },
    );

    const resize = () => {
      const layout = getAvatarCanvasLayoutSize(canvas);
      controllerRef.current?.resize(
        layout.width || 420,
        layout.height || 420,
        getAvatarCanvasRenderPixelRatio(canvas, window.devicePixelRatio || 1),
      );
    };
    resize();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(canvas);
    return () => {
      disposed = true;
      observer?.disconnect();
      unsubscribe();
      controller.dispose();
      if (controllerRef.current === controller) controllerRef.current = null;
    };
  }, [avatarManifest, rendererKind]);

  const applyCue = useCallback((cue: AvatarCue) => {
    controllerRef.current?.applyCue(cue);
  }, []);

  const startLipSync = useCallback(() => {
    controllerRef.current?.setLipSyncSource(new SyntheticLipSyncSource("sine"));
  }, []);

  const stopLipSync = useCallback(() => {
    controllerRef.current?.setLipSyncSource(new SilentLipSyncSource());
    controllerRef.current?.clearLayer("speech");
  }, []);

  const invalidateGeneration = useCallback((generationId: string) => {
    controllerRef.current?.invalidateGeneration(generationId);
    controllerRef.current?.setLipSyncSource(new SilentLipSyncSource());
    controllerRef.current?.applyCue({
      cue_id: crypto.randomUUID(),
      kind: "override",
      name: "interrupt",
      duration_ms: 420,
      priority: 100,
    });
    controllerRef.current?.applyCue({
      cue_id: crypto.randomUUID(),
      kind: "state",
      name: "idle",
      priority: 95,
    });
  }, []);

  const touch = useCallback(() => {
    controllerRef.current?.applyCue({
      cue_id: crypto.randomUUID(),
      kind: "expression",
      name: "happy",
      duration_ms: 6_000,
      priority: 55,
    });
    controllerRef.current?.applyCue({
      cue_id: crypto.randomUUID(),
      kind: "motion",
      name: "headpat",
      duration_ms: 4_500,
      priority: 60,
    });
  }, []);

  const hitTest = useCallback(
    (clientX: number, clientY: number): AvatarInteractionEvent[] => {
      const canvas = canvasRef.current;
      const controller = controllerRef.current;
      if (!canvas || !controller) return [];
      const point = mapClientPointToAvatarCanvas(canvas, clientX, clientY);
      return point ? controller.hitTest(point.x, point.y) : [];
    },
    [],
  );

  const resetAvatar = useCallback(() => {
    controllerRef.current?.setLipSyncSource(new SilentLipSyncSource());
    controllerRef.current?.reset();
  }, []);

  return {
    canvasRef,
    avatarManifest,
    snapshot,
    rendererKind,
    avatarWarning,
    applyCue,
    startLipSync,
    stopLipSync,
    invalidateGeneration,
    hitTest,
    touch,
    resetAvatar,
  };
}
