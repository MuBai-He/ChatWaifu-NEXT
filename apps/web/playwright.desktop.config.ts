import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/desktop-product.spec.ts",
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-desktop-product",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "pnpm dev:desktop --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173/desktop-pet",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      VITE_RUNTIME_URL: process.env.VITE_RUNTIME_URL || "http://127.0.0.1:8765",
      VITE_RUNTIME_TOKEN:
        process.env.VITE_RUNTIME_TOKEN ||
        process.env.CHATWAIFU_SECURITY__ADMIN_TOKEN ||
        "",
    },
  },
});
