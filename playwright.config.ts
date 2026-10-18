import { defineConfig, devices } from "@playwright/test";

const backendCommand = process.platform === "win32"
  ? "backend\\.venv\\Scripts\\python.exe -m uvicorn geoplan.bootstrap:app --host 127.0.0.1 --port 8013"
  : "uv run --directory backend --extra optimization uvicorn geoplan.bootstrap:app --host 127.0.0.1 --port 8013";

export default defineConfig({
  testDir: "./frontend/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8013",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
  ],
  webServer: {
    command: backendCommand,
    url: "http://127.0.0.1:8013/health",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
