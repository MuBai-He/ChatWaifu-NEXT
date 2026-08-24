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
import type { AvatarCue } from "@chatwaifu/protocol";
import { useCallback, useEffect, useRef, useState } from "react";

export function useChatAvatar() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<AvatarController | null>(null);
  const [snapshot, setSnapshot] = useState<AvatarControllerSnapshot | null>(
    null,
  );
  const [rendererKind, setRendererKind] = useState<"live2d" | "fake">("live2d");
  const [avatarWarning, setAvatarWarning] = useState<string | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let disposed = false;
    setSnapshot(null);
    const manifest =
      rendererKind === "live2d" ? LIVE2D_LAB_MANIFEST : AVATAR_LAB_MANIFEST;
    const renderer =
      rendererKind === "live2d"
        ? new Live2DAvatarRenderer(canvas)
        : new FakeAvatarRenderer(canvas);
    const controller = new AvatarController(renderer, manifest);
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
      const bounds = canvas.getBoundingClientRect();
      controllerRef.current?.resize(
        bounds.width || 420,
        bounds.height || 420,
        window.devicePixelRatio || 1,
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
  }, [rendererKind]);

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
      kind: "state",
      name: "idle",
      priority: 90,
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

  const resetAvatar = useCallback(() => {
    controllerRef.current?.setLipSyncSource(new SilentLipSyncSource());
    controllerRef.current?.reset();
  }, []);

  return {
    canvasRef,
    snapshot,
    rendererKind,
    avatarWarning,
    applyCue,
    startLipSync,
    stopLipSync,
    invalidateGeneration,
    touch,
    resetAvatar,
  };
}
