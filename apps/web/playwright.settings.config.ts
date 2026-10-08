import { defineConfig, devices } from "@playwright/test";

const runtime = process.env.VITE_RUNTIME_URL;
if (!runtime || !/^http:\/\/127\.0\.0\.1:(?!8765(?:$|\/))\d+$/.test(runtime)) {
  throw new Error("Settings acceptance requires a disposable loopback Runtime");
}
const env = {
  VITE_RUNTIME_URL: runtime,
  VITE_RUNTIME_TOKEN: process.env.VITE_RUNTIME_TOKEN || "",
};
export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/settings-center.spec.ts",
  workers: 1,
  retries: 0,
  use: { ...devices["Desktop Chrome"], trace: "retain-on-failure" },
  projects: [
    { name: "settings-web", use: { baseURL: "http://127.0.0.1:4186" } },
    { name: "settings-desktop", use: { baseURL: "http://127.0.0.1:4187" } },
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
