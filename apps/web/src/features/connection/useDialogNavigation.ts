import { useEffect, useRef, type KeyboardEvent } from "react";
import { acquireNativeInteractionGuard } from "../../nativeInteractionGuard";

export function useDialogNavigation<T extends HTMLElement>(
  open: boolean,
  close: () => void,
) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    const release = acquireNativeInteractionGuard("dialog");
    const target = ref.current?.querySelector<HTMLElement>(
      "[data-dialog-close], input, button",
    );
    target?.focus();
    return () => {
      release();
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, [open]);
  const onKeyDown = (event: KeyboardEvent<T>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
    if (event.key !== "Tab") return;
    const nodes = [
      ...(ref.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
      ) ?? []),
    ].filter((node) => node.getClientRects().length > 0);
    const first = nodes[0];
    const last = nodes.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };
  return { ref, onKeyDown };
}
