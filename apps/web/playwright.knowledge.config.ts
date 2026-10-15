import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "knowledge-map.spec.ts",
  fullyParallel: false,
  use: { baseURL: "http://127.0.0.1:4177", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm dev:web --host 127.0.0.1 --port 4177",
    url: "http://127.0.0.1:4177/knowledge",
    reuseExistingServer: false,
    env: { VITE_RUNTIME_URL: "", VITE_RUNTIME_TOKEN: "" },
  },
});
