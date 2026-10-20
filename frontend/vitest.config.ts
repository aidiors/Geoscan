import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": resolve(import.meta.dirname, "src") },
  },
  test: {
    include: ["frontend/src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: ["./frontend/src/test/setup.ts"],
    exclude: ["frontend/e2e/**", "node_modules/**"],
  },
});
