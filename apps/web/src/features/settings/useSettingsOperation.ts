import { useCallback, useRef, useState } from "react";

export interface SettingsNotice {
  tone: "info" | "success" | "error";
  text: string;
}

export interface SettingsOperationMessages<Result> {
  pending?: string;
  success?: string | ((result: Result) => string);
  error: string;
}

export function useSettingsOperation<Key extends string>() {
  const active = useRef<Key | null>(null);
  const [busy, setBusy] = useState<Key | null>(null);
  const [notice, setNotice] = useState<SettingsNotice | null>(null);

  const run = useCallback(
    async <Result>(
      key: Key,
      operation: () => Promise<Result>,
      messages: SettingsOperationMessages<Result>,
    ): Promise<Result | undefined> => {
      if (active.current !== null) return undefined;
      active.current = key;
      setBusy(key);
      setNotice(
        messages.pending ? { tone: "info", text: messages.pending } : null,
      );
      try {
        const result = await operation();
        const success =
          typeof messages.success === "function"
            ? messages.success(result)
            : messages.success;
        if (success) setNotice({ tone: "success", text: success });
        return result;
      } catch (error: unknown) {
        setNotice({
          tone: "error",
          text: error instanceof Error ? error.message : messages.error,
        });
        return undefined;
      } finally {
        active.current = null;
        setBusy(null);
      }
    },
    [],
  );

  return {
    busy,
    notice,
    setNotice,
    clearNotice: () => setNotice(null),
    run,
  };
}
