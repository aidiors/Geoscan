import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./frontend/src/test/setup.ts"],
    exclude: ["frontend/e2e/**", "node_modules/**"],
  },
});
