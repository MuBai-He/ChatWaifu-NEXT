import { useCallback, useEffect, useRef, useState } from "react";

import { isDesktopHost, observeDesktopRuntime } from "../chat/runtimeEndpoint";
import { bootstrapRuntimeSession } from "../chat/chatSessionBootstrap";
import {
  getHealth,
  getMemory,
  getTtsProviders,
  resetSession,
  selectTtsProvider,
} from "../chat/runtimeClient";
import type {
  CharacterProfile,
  RuntimeHealth,
  TtsProviderSnapshot,
} from "../chat/types";
import { useChatAvatar } from "../chat/useChatAvatar";

/**
 * Control-center runtime state. It intentionally does not open event/audio
 * sockets, create an AudioContext, or request microphone access. The overlay
 * remains the sole media owner.
 */
export function useSettingsRuntime() {
  const avatar = useChatAvatar();
  const activeRead = useRef<AbortController | null>(null);
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [character, setCharacter] = useState<CharacterProfile | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [connection, setConnection] = useState<
    "connecting" | "connected" | "offline"
  >("connecting");
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [ttsProviders, setTtsProviders] = useState<TtsProviderSnapshot[]>([]);
  const [ttsProviderId, setTtsProviderId] = useState("");
  const [ttsSwitching, setTtsSwitching] = useState(false);

  useEffect(() => {
    const lifetime = new AbortController();
    let timer: number | undefined;
    let identity: string | null = null;
    const invalidate = () => {
      activeRead.current?.abort();
      activeRead.current = null;
      window.clearInterval(timer);
      timer = undefined;
    };
    const connect = () => {
      invalidate();
      const controller = new AbortController();
      activeRead.current = controller;
      const { signal } = controller;
      setSessionId(null);
      setResetting(false);
      setTtsSwitching(false);
      setConnection("connecting");
      setError(null);
      void (async () => {
        try {
          const result = await bootstrapRuntimeSession(localStorage, signal);
          signal.throwIfAborted();
          const providers = await getTtsProviders(
            result.sessionId,
            signal,
          ).catch(() => []);
          signal.throwIfAborted();
          setHealth(result.health);
          setCharacter(result.character);
          setSessionId(result.sessionId);
          setTtsProviders(providers);
          setTtsProviderId(
            providers.find((provider) => provider.selected)?.provider_id ??
              providers[0]?.provider_id ??
              "",
          );
          setConnection("connected");
          setError(null);
          // Only one health read at a time; all reads belong to this boot.
          let refreshing = false;
          timer = window.setInterval(() => {
            if (refreshing || signal.aborted) return;
            refreshing = true;
            void getHealth(signal)
              .then((snapshot) => {
                if (signal.aborted) return;
                setHealth(snapshot);
                setConnection("connected");
                setError(null);
              })
              .catch((healthError: unknown) => {
                if (signal.aborted) return;
                setConnection("offline");
                setError(message(healthError, "Runtime 连接已中断"));
              })
              .finally(() => {
                refreshing = false;
              });
          }, 5_000);
        } catch (loadError: unknown) {
          if (signal.aborted) return;
          setConnection("offline");
          setError(message(loadError, "Runtime 不可用"));
        }
      })();
    };
    if (isDesktopHost()) {
      void observeDesktopRuntime((status) => {
        if (status.state === "ready") {
          const next = JSON.stringify([
            status.runtime_url,
            status.token,
            status.restart_count,
          ]);
          if (identity === next) return;
          identity = next;
          connect();
          return;
        }
        identity = null;
        invalidate();
        setSessionId(null);
        setHealth(null);
        setResetting(false);
        setTtsSwitching(false);
        const failed =
          status.state === "circuit_open" || status.state === "stopped";
        setConnection(failed ? "offline" : "connecting");
        setError(
          failed
            ? (status.detail ?? "本地 Runtime 已停止，请重启本地服务。")
            : null,
        );
      }, lifetime.signal).catch((statusError: unknown) => {
        if (lifetime.signal.aborted) return;
        invalidate();
        setConnection("offline");
        setError(message(statusError, "无法读取本地 Runtime 状态"));
      });
    } else {
      connect();
    }
    return () => {
      lifetime.abort();
      invalidate();
    };
  }, []);

  const refreshTtsProviders = useCallback(async () => {
    if (!sessionId) return;
    const signal = activeRead.current?.signal;
    if (!signal || signal.aborted) return;
    const providers = await getTtsProviders(sessionId, signal);
    if (signal.aborted) return;
    setTtsProviders(providers);
    const selected = providers.find((provider) => provider.selected);
    if (selected) setTtsProviderId(selected.provider_id);
  }, [sessionId]);

  const changeTtsProvider = useCallback(
    async (providerId: string) => {
      if (!sessionId || ttsSwitching || providerId === ttsProviderId) return;
      const signal = activeRead.current?.signal;
      if (!signal || signal.aborted) return;
      setTtsSwitching(true);
      setError(null);
      try {
        const selected = await selectTtsProvider(sessionId, providerId);
        if (signal.aborted) return;
        setTtsProviderId(selected.provider_id);
        setTtsProviders((current) =>
          current.map((provider) => ({
            ...provider,
            selected: provider.provider_id === selected.provider_id,
          })),
        );
        setHealth((current) =>
          current
            ? {
                ...current,
                providers: { ...current.providers, tts: selected.provider_id },
              }
            : current,
        );
      } catch (selectionError: unknown) {
        if (signal.aborted) return;
        setError(message(selectionError, "切换语音模型失败"));
      } finally {
        if (!signal.aborted) setTtsSwitching(false);
      }
    },
    [sessionId, ttsProviderId, ttsSwitching],
  );

  const resetAll = useCallback(async (): Promise<boolean> => {
    if (!sessionId || resetting) return false;
    const signal = activeRead.current?.signal;
    if (!signal || signal.aborted) return false;
    setResetting(true);
    setError(null);
    try {
      await resetSession(sessionId);
      if (signal.aborted) return false;
      avatar.resetAvatar();
      return true;
    } catch (resetError: unknown) {
      if (signal.aborted) return false;
      setError(message(resetError, "重置失败"));
      return false;
    } finally {
      if (!signal.aborted) setResetting(false);
    }
  }, [avatar, resetting, sessionId]);

  const refreshMemories = useCallback(async () => {
    await getMemory();
  }, []);

  return {
    canvasRef: avatar.canvasRef,
    avatarManifest: avatar.avatarManifest,
    snapshot: avatar.snapshot,
    rendererKind: avatar.rendererKind,
    health,
    character,
    sessionId,
    connection,
    error,
    resetting,
    ttsProviders,
    ttsProviderId,
    ttsSwitching,
    changeTtsProvider,
    refreshTtsProviders,
    resetAll,
    refreshMemories,
  };
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
