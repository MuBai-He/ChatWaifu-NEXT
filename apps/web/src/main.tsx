import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { resolveAppSurface } from "./appSurface.ts";

const appSurface = resolveAppSurface();
const dataSurface =
  appSurface === "desktop-pet" || appSurface === "desktop-settings"
    ? appSurface
    : "application";
document.documentElement.dataset.surface = dataSurface;
document.body.dataset.surface = dataSurface;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
