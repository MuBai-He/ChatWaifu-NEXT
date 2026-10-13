import { defineConfig, devices } from "@playwright/test";

const runtime = process.env.PASSWORD_E2E_RUNTIME_URL;
if (!runtime || !/^http:\/\/127\.0\.0\.1:(?!8765(?:$|\/))\d+$/.test(runtime))
  throw new Error(
    "Password acceptance requires a fresh disposable loopback Runtime",
  );

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/runtime-password.spec.ts",
  workers: 1,
  retries: 0,
  use: {
    baseURL: process.env.PASSWORD_E2E_WEB_URL || "http://127.0.0.1:14173",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "password-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: process.env.PASSWORD_E2E_WEB_URL
    ? undefined
    : {
        command: "pnpm dev:web --host 127.0.0.1 --port 14173 --strictPort",
        url: "http://127.0.0.1:14173",
        reuseExistingServer: false,
        // Exercise the real first-launch chooser, without the development bypass.
        env: { VITE_RUNTIME_URL: "", VITE_RUNTIME_TOKEN: "" },
      },
});
