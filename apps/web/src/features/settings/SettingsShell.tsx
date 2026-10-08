import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { SettingsIcon } from "./SettingsIcon";
import type { SettingsSectionDefinition } from "./settingsRegistry";
import { settingsSectionAvailability } from "./settingsRegistry";
import "./settings-shell.css";

const groups = [
  "聊天",
  "模型与声音",
  "能力与任务",
  "记忆与资料",
  "日程",
  "连接与设备",
];

export function SettingsShell<Context>({
  sections,
  selectedId,
  onSelect,
  context,
  connection,
  subtitle,
  footer,
  children,
  className = "",
}: {
  sections: readonly SettingsSectionDefinition<Context>[];
  selectedId: string;
  onSelect: (id: string) => void;
  context: Context;
  connection: "connected" | "connecting" | "offline";
  subtitle: string;
  className?: string;
  footer?: ReactNode;
  children: ReactNode;
}) {
  const [query, setQuery] = useState("");
  const scroll = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, number>());
  useLayoutEffect(() => {
    if (scroll.current)
      scroll.current.scrollTop = positions.current.get(selectedId) ?? 0;
  }, [selectedId]);
  const selected =
    sections.find((section) => section.id === selectedId) ?? sections[0];
  const normalized = query.trim().toLocaleLowerCase();
  const results = sections.filter((section) =>
    [
      section.label,
      section.description,
      section.group,
      ...(section.keywords ?? []),
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(normalized),
  );
  return (
    <main className={`settings-shell settings-controls ${className}`}>
      <aside className="settings-sidebar">
        <header className="settings-brand">
          <span className="desktop-settings-app-icon">
            <SettingsIcon name="brand" />
          </span>
          <div>
            <strong>ChatWaifu NEXT</strong>
            <small>{subtitle}</small>
          </div>
        </header>
        <label className="settings-search">
          <span>查找设置</span>
          <input
            type="search"
            aria-label="查找设置"
            placeholder="搜索自由交流、Jev、打字…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <nav aria-label="设置分类" className="settings-navigation">
          {groups.map((group) => {
            const items = results.filter(
              (section) => (section.group ?? "连接与设备") === group,
            );
            return items.length ? (
              <div className="settings-nav-group" key={group}>
                <span className="settings-nav-caption">{group}</span>
                {items.map((section) => {
                  const availability = settingsSectionAvailability(
                    section,
                    context,
                  );
                  return (
                    <button
                      type="button"
                      key={section.id}
                      disabled={!availability.enabled}
                      title={availability.reason}
                      aria-label={`${section.label} ${availability.reason ?? section.description}`}
                      aria-current={
                        selected?.id === section.id ? "page" : undefined
                      }
                      onClick={() => {
                        positions.current.set(
                          selectedId,
                          scroll.current?.scrollTop ?? 0,
                        );
                        onSelect(section.id);
                        setQuery("");
                      }}
                    >
                      <SettingsIcon name={section.icon} />
                      <span>
                        <strong>{section.label}</strong>
                        <small>
                          {availability.reason ?? section.description}
                        </small>
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null;
          })}
          {!results.length && (
            <p className="settings-search-empty" role="status">
              没有匹配的设置。试试“模型”或“QQ”。
            </p>
          )}
        </nav>
        <footer>{footer}</footer>
      </aside>
      <section className="settings-workspace">
        <header className="settings-heading">
          <div>
            <small>{selected?.group ?? "设置"}</small>
            <h1>{selected?.label}</h1>
            <p>{selected?.description}</p>
          </div>
          <span className={`settings-connection ${connection}`} role="status">
            <i />
            {connection === "connected"
              ? "已连接"
              : connection === "offline"
                ? "连接已断开"
                : "正在连接"}
          </span>
        </header>
        <div ref={scroll} className="settings-scroll desktop-settings-scroll">
          {children}
        </div>
      </section>
    </main>
  );
}
