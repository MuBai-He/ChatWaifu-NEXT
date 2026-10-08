import { useState, useSyncExternalStore } from "react";
import { ProductIcon } from "../../components/ProductIcon";
import {
  getRuntimeContextRevision,
  isRemoteRuntime,
  subscribeRuntimeContext,
} from "../chat/runtimeEndpoint";
import { useDesktopPreferences } from "../desktop-pet/useDesktopPreferences";
import { SettingsShell } from "../settings/SettingsShell";
import { settingsContext } from "../settings/SettingsRuntimeContext";
import type { SettingsRuntimeContext } from "./DesktopSettingsContext";
import { DesktopOnboardingDialog } from "./DesktopOnboardingDialog";
import {
  completeDesktopOnboarding,
  isDesktopOnboardingCompleted,
} from "./desktopOnboarding";
import {
  desktopSettingsRegistry,
  type DesktopSettingsSectionId,
} from "./desktopSettingsRegistry";
import { visibleSettingsSections } from "./settingsRegistry";
import { useSettingsRuntime } from "./useSettingsRuntime";

export function DesktopSettingsPage() {
  const epoch = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  return <DesktopSettingsContent key={epoch} />;
}

function DesktopSettingsContent() {
  const runtime = useSettingsRuntime();
  const desktop = useDesktopPreferences();
  const [sectionId, setSectionId] =
    useState<DesktopSettingsSectionId>("appearance");
  const [visited, setVisited] = useState(() => new Set<string>(["appearance"]));
  const [onboardingOpen, setOnboardingOpen] = useState(
    () => desktop.desktopHost && !isDesktopOnboardingCompleted(),
  );
  const context: SettingsRuntimeContext = {
    ...settingsContext(runtime),
    desktop,
  };
  const sections = visibleSettingsSections(
    desktopSettingsRegistry,
    context,
    desktop.desktopHost ? "desktop" : "browser",
  );
  const select = (id: string) => {
    setSectionId(id as DesktopSettingsSectionId);
    setVisited((current) => new Set([...current, id]));
  };
  return (
    <>
      <SettingsShell
        className="desktop-settings-page"
        sections={sections}
        selectedId={sectionId}
        onSelect={select}
        context={context}
        connection={runtime.connection}
        subtitle="桌面设置中心"
        footer={
          <button
            className="settings-guide"
            type="button"
            onClick={(event) => {
              event.currentTarget.focus();
              setOnboardingOpen(true);
            }}
          >
            <ProductIcon name="story" />
            <span>
              新手引导<small>API、声音与麦克风</small>
            </span>
          </button>
        }
      >
        {sections
          .filter((section) => visited.has(section.id))
          .map((section) => {
            const Component = section.component;
            return (
              <div key={section.id} hidden={section.id !== sectionId}>
                <Component
                  context={context}
                  active={section.id === sectionId}
                />
              </div>
            );
          })}
        {(context.runtime.error || desktop.error) && (
          <p className="settings-error" role="alert">
            {desktop.error ?? context.runtime.error}
            {runtime.connection === "offline" && (
              <button onClick={runtime.reconnect}>立即重连</button>
            )}
          </p>
        )}
      </SettingsShell>
      <DesktopOnboardingDialog
        open={onboardingOpen && !isRemoteRuntime()}
        onDefer={() => setOnboardingOpen(false)}
        onComplete={() => {
          completeDesktopOnboarding();
          setOnboardingOpen(false);
        }}
        onNavigate={(section) => {
          select(section);
          setOnboardingOpen(false);
        }}
      />
    </>
  );
}
