import { ConversationScopeGate } from "../../features/chat/ConversationScopeGate";
import { AvatarLabPage } from "../../features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "../../features/chat/ChatDemoPage";
import { lazy, Suspense } from "react";

const WebSettingsPage = lazy(() =>
  import("../../features/settings/WebSettingsPage").then((module) => ({
    default: module.WebSettingsPage,
  })),
);
import { resolveWebSurface, type WebSurface } from "./webSurface";

interface WebProductAppProps {
  surface?: WebSurface;
}

export function WebProductApp({
  surface = resolveWebSurface(window.location.pathname),
}: WebProductAppProps) {
  return surface === "avatar-lab" ? (
    <AvatarLabPage />
  ) : (
    <ConversationScopeGate showSwitch={surface === "application"}>
      {surface === "channels-settings" ||
      surface === "agent-settings" ||
      surface === "settings" ? (
        <Suspense
          fallback={
            <main className="settings-loading" role="status">
              正在打开设置中心…
            </main>
          }
        >
          <WebSettingsPage
            initialSection={
              window.location.pathname.split("/")[2] || "channels"
            }
          />
        </Suspense>
      ) : (
        <ChatDemoPage />
      )}
    </ConversationScopeGate>
  );
}
