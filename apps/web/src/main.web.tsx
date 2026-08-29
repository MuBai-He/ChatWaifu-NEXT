import "./index.css";
import "./features/chat/chat-demo.css";
import "./features/chat/skill-confirmation-prompt.css";

import { mountProduct } from "./product/mountProduct";
import { resolveWebSurface, WebProductApp } from "./product/web/WebProductApp";

const surface = resolveWebSurface(window.location.pathname);
mountProduct({
  product: "web",
  surface,
  children: <WebProductApp surface={surface} />,
});
