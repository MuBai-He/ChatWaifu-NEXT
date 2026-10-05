import { ConversationScopeGate } from "../../features/chat/ConversationScopeGate";
import { AvatarLabPage } from "../../features/avatar-lab/AvatarLabPage";
import { ChatDemoPage } from "../../features/chat/ChatDemoPage";
import { ChannelsSettingsPage } from "../../features/channels-settings/ChannelsSettingsPage";
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
    <ConversationScopeGate showSwitch={surface !== "channels-settings"}>
      {surface === "channels-settings" ? (
        <ChannelsSettingsPage />
      ) : (
        <ChatDemoPage />
      )}
    </ConversationScopeGate>
  );
}
