import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // Real Cubism/WebGL cases are intentionally serialized in this shared local
  // server: parallel contexts can starve requestAnimationFrame long enough to
  // hide short semantic cues and create machine-load-dependent results.
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
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "pnpm dev --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173/avatar-lab",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
