import { useCallback, useEffect, useState } from "react";

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
    let disposed = false;
    void bootstrapRuntimeSession()
      .then(async (result) => {
        if (disposed) return;
        const providers = await getTtsProviders(result.sessionId).catch(
          () => [],
        );
        if (disposed) return;
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
      })
      .catch((loadError: unknown) => {
        if (disposed) return;
        setConnection("offline");
        setError(message(loadError, "Runtime 不可用"));
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    let disposed = false;
    const refreshHealth = () => {
      void getHealth()
        .then((snapshot) => {
          if (disposed) return;
          setHealth(snapshot);
          setConnection("connected");
        })
        .catch((healthError: unknown) => {
          if (disposed) return;
          setConnection("offline");
          setError(message(healthError, "Runtime 连接已中断"));
        });
    };
    const timer = window.setInterval(refreshHealth, 5_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [sessionId]);

  const refreshTtsProviders = useCallback(async () => {
    if (!sessionId) return;
    const providers = await getTtsProviders(sessionId);
    setTtsProviders(providers);
    const selected = providers.find((provider) => provider.selected);
    if (selected) setTtsProviderId(selected.provider_id);
  }, [sessionId]);

  const changeTtsProvider = useCallback(
    async (providerId: string) => {
      if (!sessionId || ttsSwitching || providerId === ttsProviderId) return;
      setTtsSwitching(true);
      setError(null);
      try {
        const selected = await selectTtsProvider(sessionId, providerId);
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
        setError(message(selectionError, "切换语音模型失败"));
      } finally {
        setTtsSwitching(false);
      }
    },
    [sessionId, ttsProviderId, ttsSwitching],
  );

  const resetAll = useCallback(async (): Promise<boolean> => {
    if (!sessionId || resetting) return false;
    setResetting(true);
    setError(null);
    try {
      await resetSession(sessionId);
      avatar.resetAvatar();
      return true;
    } catch (resetError: unknown) {
      setError(message(resetError, "重置失败"));
      return false;
    } finally {
      setResetting(false);
    }
  }, [avatar, resetting, sessionId]);

  const refreshMemories = useCallback(async () => {
    await getMemory();
  }, []);

  return {
    canvasRef: avatar.canvasRef,
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
