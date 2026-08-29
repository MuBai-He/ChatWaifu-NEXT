import "./index.css";
import "./features/chat/skill-confirmation-prompt.css";
import "./features/desktop-pet/desktop-pet.css";
import "./features/desktop-settings/desktop-settings.css";

import { DesktopProductApp } from "./product/desktop/DesktopProductApp";
import { resolveDesktopSurface } from "./product/desktop/desktopSurface";
import { mountProduct } from "./product/mountProduct";

const surface = resolveDesktopSurface();
mountProduct({
  product: "desktop",
  surface,
  children: <DesktopProductApp surface={surface} />,
});
