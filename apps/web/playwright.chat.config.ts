import { defineConfig } from "@playwright/test";
import settings from "./playwright.settings.config.ts";

const servers = Array.isArray(settings.webServer) ? settings.webServer : [];

export default defineConfig({
  ...settings,
  testMatch: "**/chat-interface.spec.ts",
  projects: [
    {
      name: "chat-chromium",
      use: { browserName: "chromium", baseURL: "http://127.0.0.1:4186" },
    },
    {
      name: "chat-webkit",
      use: { browserName: "webkit", baseURL: "http://127.0.0.1:4186" },
    },
  ],
  webServer: servers.slice(0, 1),
});
