import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ProductIcon } from "../../components/ProductIcon";
import "./settings-controls.css";
import "./settings-glass.css";

/** The same settings material and controls remain available outside the shell. */
export function SettingsDialog({
  title,
  description,
  label,
  closeLabel,
  className = "",
  returnFocusTo,
  onClose,
  children,
}: {
  title: string;
  description: string;
  label: string;
  closeLabel: string;
  className?: string;
  returnFocusTo?: HTMLElement | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const overlay = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const previous = returnFocusTo ?? document.activeElement;
    const siblings = [...document.body.children].filter(
      (element): element is HTMLElement =>
        element instanceof HTMLElement && element !== overlay.current,
    );
    const inert = siblings.map((element) => element.inert);
    siblings.forEach((element) => (element.inert = true));
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    overlay.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const keydown = (event: KeyboardEvent) => {
      // A Runtime confirmation can sit above this dialog and own the keyboard.
      if (overlay.current?.inert) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
      }
      if (event.key !== "Tab") return;
      const controls = [
        ...(overlay.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]',
        ) ?? []),
      ].filter(
        (element) =>
          element.tabIndex >= 0 && element.getClientRects().length > 0,
      );
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", keydown, true);
    return () => {
      document.removeEventListener("keydown", keydown, true);
      siblings.forEach(
        (element, index) => (element.inert = inert[index] ?? false),
      );
      document.body.style.overflow = overflow;
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, [returnFocusTo]);
  return createPortal(
    <div
      ref={overlay}
      className="settings-dialog-overlay settings-material settings-surface settings-controls"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className={`settings-dialog ${className}`}
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        <header className="settings-dialog-heading">
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
          <button type="button" aria-label={closeLabel} onClick={onClose}>
            <ProductIcon name="close" />
          </button>
        </header>
        <div className="settings-dialog-body">{children}</div>
      </section>
    </div>,
    document.body,
  );
}
