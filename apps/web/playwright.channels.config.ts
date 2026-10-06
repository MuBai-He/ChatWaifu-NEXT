import { defineConfig, devices } from "@playwright/test";

// Writes must only target an explicitly selected, disposable local Runtime.
const runtime = process.env.VITE_RUNTIME_URL;
if (
  !runtime ||
  !/^http:\/\/127\.0\.0\.1:(?!8765(?:$|\/))\d+$/.test(runtime) ||
  process.env.CHATWAIFU_E2E_ISOLATED_CHANNELS !== "1"
) {
  throw new Error(
    "Channel settings E2E requires an isolated loopback Runtime and CHATWAIFU_E2E_ISOLATED_CHANNELS=1",
  );
}
const env = {
  VITE_RUNTIME_URL: runtime,
  VITE_RUNTIME_TOKEN: process.env.VITE_RUNTIME_TOKEN || "",
};
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/channel-settings.spec.ts",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: { ...devices["Desktop Chrome"], trace: "retain-on-failure" },
  projects: [
    { name: "channels-web", use: { baseURL: "http://127.0.0.1:4183" } },
    { name: "channels-desktop", use: { baseURL: "http://127.0.0.1:4184" } },
    {
      name: "channels-desktop-webkit",
      use: {
        ...devices["Desktop Safari"],
        baseURL: "http://127.0.0.1:4184",
      },
      grep: /settings controls/,
    },
  ],
  webServer: [
    {
      command: "pnpm dev:web --host 127.0.0.1 --port 4183 --strictPort",
      url: "http://127.0.0.1:4183",
      reuseExistingServer: false,
      env,
    },
    {
      command: "pnpm dev:desktop --host 127.0.0.1 --port 4184 --strictPort",
      url: "http://127.0.0.1:4184/desktop-settings",
      reuseExistingServer: false,
      env,
    },
  ],
});
