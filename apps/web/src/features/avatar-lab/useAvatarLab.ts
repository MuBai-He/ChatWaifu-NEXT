import {
  AVATAR_LAB_MANIFEST,
  AvatarController,
  AvatarRendererError,
  FakeAvatarRenderer,
  LIVE2D_LAB_MANIFEST,
  Live2DAvatarRenderer,
  SilentLipSyncSource,
  type AvatarControllerSnapshot,
  type AvatarManifest,
  type AvatarWarning,
  type LipSyncSource,
} from "@chatwaifu/avatar-sdk";
import type { AvatarCue, AvatarInteractionEvent } from "@chatwaifu/protocol";
import { useCallback, useEffect, useRef, useState } from "react";

export type RendererKind = "fake" | "live2d";

export function useAvatarLab(rendererKind: RendererKind) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<AvatarController | null>(null);
  const generationId = useRef(crypto.randomUUID());
  const [snapshot, setSnapshot] = useState<AvatarControllerSnapshot | null>(
    null,
  );
  const [error, setError] = useState<AvatarWarning | null>(null);
  const [interactions, setInteractions] = useState<AvatarInteractionEvent[]>(
    [],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setError(null);
    setSnapshot(null);
    setInteractions([]);

    const manifest: AvatarManifest =
      rendererKind === "fake" ? AVATAR_LAB_MANIFEST : LIVE2D_LAB_MANIFEST;
    const controllerHolder: { current: AvatarController | null } = {
      current: null,
    };
    const renderer =
      rendererKind === "fake"
        ? new FakeAvatarRenderer(canvas)
        : new Live2DAvatarRenderer(canvas, {
            onMotionEnded: (cueId) =>
              controllerHolder.current?.notifyMotionEnded(cueId),
            onWarning: setError,
          });
    const controller = new AvatarController(renderer, manifest);
    controllerHolder.current = controller;
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);
    const unsubscribeInteraction = controller.onInteraction((interaction) => {
      setInteractions((current) => [interaction, ...current].slice(0, 8));
    });
    void controller.load().catch((loadError: unknown) => {
      const warning =
        loadError instanceof AvatarRendererError
          ? loadError.toWarning()
          : {
              code: "avatar.renderer_load_failed",
              message:
                loadError instanceof Error
                  ? loadError.message
                  : "Avatar renderer failed to load.",
              action: "Inspect Avatar Lab diagnostics.",
            };
      setError(warning);
    });

    return () => {
      unsubscribe();
      unsubscribeInteraction();
      controller.dispose();
      controllerHolder.current = null;
      if (controllerRef.current === controller) controllerRef.current = null;
    };
  }, [rendererKind]);

  const sendCue = useCallback(
    (
      kind: AvatarCue["kind"],
      name: string,
      options: Partial<AvatarCue> = {},
    ) => {
      controllerRef.current?.applyCue({
        cue_id: crypto.randomUUID(),
        generation_id: generationId.current,
        kind,
        name,
        ...options,
      });
    },
    [],
  );

  const startSpeaking = useCallback(
    (source: LipSyncSource) => {
      controllerRef.current?.setLipSyncSource(source);
      sendCue("state", "speaking", { priority: 70 });
      sendCue("speech", "speaking", { priority: 70 });
    },
    [sendCue],
  );

  const stopSpeaking = useCallback(() => {
    controllerRef.current?.setLipSyncSource(new SilentLipSyncSource());
    controllerRef.current?.clearLayer("speech");
    sendCue("state", "idle", { priority: 80 });
  }, [sendCue]);

  const reset = useCallback(() => {
    controllerRef.current?.setLipSyncSource(new SilentLipSyncSource());
    controllerRef.current?.reset();
  }, []);

  const resize = useCallback((width: number, height: number, dpr: number) => {
    controllerRef.current?.resize(width, height, dpr);
  }, []);

  const handlePointer = useCallback((x: number, y: number) => {
    controllerRef.current?.handlePointer(x, y);
  }, []);

  return {
    canvasRef,
    controllerRef,
    snapshot,
    error,
    interactions,
    sendCue,
    startSpeaking,
    stopSpeaking,
    reset,
    resize,
    handlePointer,
  };
}
