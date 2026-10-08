import type { KeyboardEvent } from "react";

export function SettingsTabs<Id extends string>({
  label,
  prefix,
  tabs,
  selected,
  onSelect,
}: {
  label: string;
  prefix: string;
  tabs: readonly { id: Id; label: string }[];
  selected: Id;
  onSelect: (id: Id) => void;
}) {
  const keydown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft")
      next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    const tab = tabs[next];
    if (!tab) return;
    event.preventDefault();
    const buttons =
      event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
        '[role="tab"]',
      );
    buttons?.[next]?.focus();
    onSelect(tab.id);
  };
  return (
    <div className="extensions-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`${prefix}-tab-${tab.id}`}
          aria-selected={selected === tab.id}
          aria-controls={`${prefix}-content-${tab.id}`}
          tabIndex={selected === tab.id ? 0 : -1}
          onClick={() => onSelect(tab.id)}
          onKeyDown={(event) => keydown(event, index)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
