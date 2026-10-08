import { defineConfig, devices } from "@playwright/test";

const runtime = process.env.VITE_RUNTIME_URL;
if (
  !runtime ||
  !/^http:\/\/127\.0\.0\.1:(?!8765(?:$|\/))\d+$/.test(runtime) ||
  process.env.CHATWAIFU_E2E_AGENT !== "1"
) {
  throw new Error("Agent browser checks require a disposable loopback Runtime");
}
const env = {
  VITE_RUNTIME_URL: runtime,
  VITE_RUNTIME_TOKEN: process.env.VITE_RUNTIME_TOKEN || "",
};
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/agent-settings.spec.ts",
  workers: 1,
  retries: 0,
  use: { ...devices["Desktop Chrome"], trace: "retain-on-failure" },
  projects: [
    { name: "agent-web", use: { baseURL: "http://127.0.0.1:4186" } },
    { name: "agent-desktop", use: { baseURL: "http://127.0.0.1:4187" } },
  ],
  webServer: [
    {
      command: "pnpm dev:web --host 127.0.0.1 --port 4186 --strictPort",
      url: "http://127.0.0.1:4186",
      reuseExistingServer: false,
      env,
    },
    {
      command: "pnpm dev:desktop --host 127.0.0.1 --port 4187 --strictPort",
      url: "http://127.0.0.1:4187/desktop-settings",
      reuseExistingServer: false,
      env,
    },
  ],
});
