import { defineConfig } from "@playwright/test";
import settings from "./playwright.settings.config.ts";

const servers = Array.isArray(settings.webServer) ? settings.webServer : [];

export default defineConfig({
  ...settings,
  testMatch: ["**/settings-center.spec.ts", "**/settings-glass.spec.ts"],
  timeout: 60_000,
  projects: [
    {
      name: "settings-web",
      use: { browserName: "chromium", baseURL: "http://127.0.0.1:4186" },
    },
    {
      name: "settings-desktop",
      use: { browserName: "chromium", baseURL: "http://127.0.0.1:4187" },
    },
    {
      name: "settings-web-webkit",
      use: { browserName: "webkit", baseURL: "http://127.0.0.1:4186" },
    },
    {
      name: "settings-desktop-webkit",
      use: { browserName: "webkit", baseURL: "http://127.0.0.1:4187" },
    },
  ],
  webServer: servers.map((server) => ({
    ...server,
    // Reuse is opt-in and intended only for the known disposable preview.
    reuseExistingServer: process.env.CHATWAIFU_REUSE_PREVIEW === "1",
  })),
});
