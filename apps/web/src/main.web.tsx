import { RuntimeConnectionGate } from "./features/connection/RuntimeConnectionGate";
import "./index.css";
import "./features/chat/chat-demo.css";
import "./features/chat/skill-confirmation-prompt.css";

import { mountProduct } from "./product/mountProduct";
import { WebProductApp } from "./product/web/WebProductApp";
import { resolveWebSurface } from "./product/web/webSurface";
import "./features/settings/settings-controls.css";

const surface = resolveWebSurface(window.location.pathname);
const embeddedKnowledge =
  surface === "knowledge" &&
  new URLSearchParams(window.location.search).get("embedded") === "1";
mountProduct({
  product: "web",
  surface,
  children:
    surface === "avatar-lab" || embeddedKnowledge ? (
      <WebProductApp surface={surface} />
    ) : (
      <RuntimeConnectionGate showSwitch={surface === "application"}>
        <WebProductApp surface={surface} />
      </RuntimeConnectionGate>
    ),
});
