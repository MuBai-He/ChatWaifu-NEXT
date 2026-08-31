import { useState } from "react";

import { useDesktopPreferences } from "../desktop-pet/useDesktopPreferences";
import type { SettingsRuntimeContext } from "./DesktopSettingsContext";
import {
  desktopSettingsRegistry,
  type DesktopSettingsSectionId,
} from "./desktopSettingsRegistry";
import { SettingsIcon } from "./SettingsIcon";
import {
  settingsSectionAvailability,
  visibleSettingsSections,
} from "./settingsRegistry";
import { useSettingsRuntime } from "./useSettingsRuntime";

export function DesktopSettingsPage() {
  const runtime = useSettingsRuntime();
  const desktop = useDesktopPreferences();
  const [sectionId, setSectionId] =
    useState<DesktopSettingsSectionId>("appearance");

  const resetConversationAndMemory = async () => {
    return runtime.resetAll();
  };
  const context: SettingsRuntimeContext = {
    canvasRef: runtime.canvasRef,
    appearance: {
      snapshot: runtime.snapshot,
      rendererKind: runtime.rendererKind,
      character: runtime.character,
    },
    voice: {
      sessionId: runtime.sessionId,
      ttsProviders: runtime.ttsProviders,
      ttsProviderId: runtime.ttsProviderId,
      ttsSwitching: runtime.ttsSwitching,
      changeTtsProvider: runtime.changeTtsProvider,
      refreshTtsProviders: runtime.refreshTtsProviders,
    },
    data: {
      sessionId: runtime.sessionId,
      resetting: runtime.resetting,
      refreshMemories: runtime.refreshMemories,
    },
    runtime: {
      connection: runtime.connection,
      health: runtime.health,
      error: runtime.error,
    },
    sessionId: runtime.sessionId,
    desktop,
    resetConversationAndMemory,
  };
  const surface = desktop.desktopHost ? "desktop" : "browser";
  const sections = visibleSettingsSections(
    desktopSettingsRegistry,
    context,
    surface,
  );
  const selected =
    sections.find((section) => section.id === sectionId) ?? sections[0];
  if (!selected) return null;
  const SelectedSection = selected.component;

  return (
    <main className="desktop-settings-page">
      <aside className="desktop-settings-sidebar">
        <header>
          <span className="desktop-settings-app-icon">
            <SettingsIcon name="brand" />
          </span>
          <div>
            <strong>ChatWaifu NEXT</strong>
            <small>桌宠设置</small>
          </div>
        </header>

        <nav aria-label="设置分类">
          {sections.map((section) => {
            const availability = settingsSectionAvailability(section, context);
            return (
              <button
                className={selected.id === section.id ? "active" : ""}
                type="button"
                key={section.id}
                disabled={!availability.enabled}
                title={availability.reason}
                onClick={() =>
                  setSectionId(section.id as DesktopSettingsSectionId)
                }
                aria-current={selected.id === section.id ? "page" : undefined}
              >
                <span>
                  <SettingsIcon name={section.icon} />
                </span>
                <div>
                  <strong>{section.label}</strong>
                  <small>{availability.reason ?? section.description}</small>
                </div>
              </button>
            );
          })}
        </nav>

        <footer>
          <i className={context.runtime.connection} />
          <div>
            <strong>{connectionLabel(context.runtime.connection)}</strong>
            <small>
              {context.runtime.health?.version
                ? `Runtime ${context.runtime.health.version}`
                : "本地服务"}
            </small>
          </div>
        </footer>
      </aside>

      <section className="desktop-settings-content">
        <header className="desktop-settings-heading">
          <div>
            <small>{selected.description}</small>
            <h1>{selected.label}</h1>
          </div>
          <span
            className={`desktop-settings-runtime ${context.runtime.connection}`}
          >
            <i />
            {context.runtime.connection === "connected"
              ? "运行正常"
              : connectionLabel(context.runtime.connection)}
          </span>
        </header>

        <div className="desktop-settings-scroll">
          <SelectedSection context={context} />

          {context.runtime.error || desktop.error ? (
            <p className="desktop-settings-error" role="alert">
              {desktop.error ?? context.runtime.error}
            </p>
          ) : null}
        </div>
      </section>
    </main>
  );
}

function connectionLabel(
  connection: "connecting" | "connected" | "offline",
): string {
  if (connection === "connected") return "已连接";
  if (connection === "connecting") return "正在连接";
  return "Runtime 离线";
}
