import { RuntimeConnectionGate } from "./features/connection/RuntimeConnectionGate";
import "./index.css";
import "./features/chat/chat-demo.css";
import "./features/chat/skill-confirmation-prompt.css";

import { mountProduct } from "./product/mountProduct";
import { WebProductApp } from "./product/web/WebProductApp";
import { resolveWebSurface } from "./product/web/webSurface";
import "./features/settings/settings-controls.css";

const surface = resolveWebSurface(window.location.pathname);
mountProduct({
  product: "web",
  surface,
  children:
    surface === "avatar-lab" ? (
      <WebProductApp surface={surface} />
    ) : (
      <RuntimeConnectionGate showSwitch={surface !== "channels-settings"}>
        <WebProductApp surface={surface} />
      </RuntimeConnectionGate>
    ),
});
