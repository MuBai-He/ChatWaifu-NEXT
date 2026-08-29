import { useCallback, type RefObject } from "react";

import {
  getMemory,
  getTtsProviders,
  interrupt,
  resetSession,
  selectTtsProvider,
  submitText,
} from "./runtimeClient";
import type { MemoryItem, TtsProviderSnapshot } from "./types";

interface ChatSessionCommandOptions {
  sessionId: string | null;
  connection: "connecting" | "connected" | "offline";
  activeGenerationRef: RefObject<string | null>;
  resetting: boolean;
  ttsSwitching: boolean;
  ttsProviderId: string;
  primePlayback: () => void;
  stopText: (generationId?: string) => void;
  stopAudio: (generationId?: string) => void;
  resetSubtitles: () => void;
  setError: (error: string | null) => void;
  setResetting: (resetting: boolean) => void;
  setTtsSwitching: (switching: boolean) => void;
  onTtsProviders: (providers: TtsProviderSnapshot[]) => void;
  onTtsSelected: (providerId: string) => void;
  onMemories: (memories: MemoryItem[]) => void;
  onReset: () => void;
}

/**
 * Owns user-issued session commands and their cancellation ordering. Runtime
 * events remain in the reducer/socket layer, so commands cannot mutate event
 * state behind that boundary.
 */
export function useChatSessionCommands({
  sessionId,
  connection,
  activeGenerationRef,
  resetting,
  ttsSwitching,
  ttsProviderId,
  primePlayback,
  stopText,
  stopAudio,
  resetSubtitles,
  setError,
  setResetting,
  setTtsSwitching,
  onTtsProviders,
  onTtsSelected,
  onMemories,
  onReset,
}: ChatSessionCommandOptions) {
  const cancelActiveOutput = useCallback(() => {
    const generationId = activeGenerationRef.current;
    if (generationId) {
      stopText(generationId);
      stopAudio(generationId);
    }
    activeGenerationRef.current = null;
  }, [activeGenerationRef, stopAudio, stopText]);

  const send = useCallback(
    async (text: string) => {
      const normalized = text.trim();
      if (!sessionId || !normalized) return;
      primePlayback();
      if (connection !== "connected") {
        setError("Runtime 事件通道尚未连接，请稍等片刻再发送。");
        return;
      }
      setError(null);
      cancelActiveOutput();
      try {
        await submitText(sessionId, normalized);
      } catch (error: unknown) {
        setError(errorMessage(error, "消息发送失败"));
      }
    },
    [cancelActiveOutput, connection, primePlayback, sessionId, setError],
  );

  const interruptActive = useCallback(async () => {
    if (!sessionId) return;
    cancelActiveOutput();
    try {
      await interrupt(sessionId);
    } catch (error: unknown) {
      setError(errorMessage(error, "打断失败"));
    }
  }, [cancelActiveOutput, sessionId, setError]);

  const changeTtsProvider = useCallback(
    async (providerId: string) => {
      if (!sessionId || ttsSwitching || providerId === ttsProviderId) return;
      setTtsSwitching(true);
      setError(null);
      cancelActiveOutput();
      try {
        const selected = await selectTtsProvider(sessionId, providerId);
        onTtsSelected(selected.provider_id);
      } catch (error: unknown) {
        setError(errorMessage(error, "切换语音模型失败"));
      } finally {
        setTtsSwitching(false);
      }
    },
    [
      cancelActiveOutput,
      onTtsSelected,
      sessionId,
      setError,
      setTtsSwitching,
      ttsProviderId,
      ttsSwitching,
    ],
  );

  const refreshTtsProviders = useCallback(async () => {
    if (!sessionId) return;
    const providers = await getTtsProviders(sessionId);
    onTtsProviders(providers);
    const selected = providers.find((provider) => provider.selected);
    if (selected) onTtsSelected(selected.provider_id);
  }, [onTtsProviders, onTtsSelected, sessionId]);

  const resetAll = useCallback(async (): Promise<boolean> => {
    if (!sessionId || resetting) return false;
    setResetting(true);
    setError(null);
    cancelActiveOutput();
    resetSubtitles();
    try {
      await resetSession(sessionId);
      onReset();
      return true;
    } catch (error: unknown) {
      setError(errorMessage(error, "重置失败"));
      return false;
    } finally {
      setResetting(false);
    }
  }, [
    cancelActiveOutput,
    onReset,
    resetSubtitles,
    resetting,
    sessionId,
    setError,
    setResetting,
  ]);

  const refreshMemories = useCallback(async () => {
    onMemories(await getMemory());
  }, [onMemories]);

  return {
    send,
    interruptActive,
    changeTtsProvider,
    refreshTtsProviders,
    resetAll,
    refreshMemories,
  };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
