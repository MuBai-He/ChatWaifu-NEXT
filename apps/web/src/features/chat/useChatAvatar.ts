import {
  AVATAR_LAB_MANIFEST,
  AvatarController,
  FakeAvatarRenderer,
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

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = new FakeAvatarRenderer(canvas);
    const controller = new AvatarController(renderer, AVATAR_LAB_MANIFEST);
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);
    void controller.load();

    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      controller.resize(
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
      observer?.disconnect();
      unsubscribe();
      controller.dispose();
      controllerRef.current = null;
    };
  }, []);

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
      duration_ms: 1500,
      priority: 55,
    });
  }, []);

  return {
    canvasRef,
    snapshot,
    applyCue,
    startLipSync,
    stopLipSync,
    invalidateGeneration,
    touch,
  };
}
