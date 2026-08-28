import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: [
      "src/**/*.test.{ts,tsx}",
      path.resolve(
        import.meta.dirname,
        "../../tools/live2d_bridge/src/**/*.test.ts",
      ),
    ],
  },
});
