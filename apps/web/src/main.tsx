import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";

const surface =
  window.location.pathname === "/desktop-pet" ? "desktop-pet" : "application";
document.documentElement.dataset.surface = surface;
document.body.dataset.surface = surface;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
