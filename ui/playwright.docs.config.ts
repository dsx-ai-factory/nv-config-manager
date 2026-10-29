import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";
const WEB_SERVER_COMMAND = process.env.CI ? "npm run start" : "npm run dev";

export default defineConfig({
  testDir: "./tests/docs-screenshots",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  timeout: 60000,

  use: {
    baseURL: BASE_URL,
    screenshot: "off",
    trace: "off",
    viewport: { width: 1280, height: 900 },
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    command: process.env.PLAYWRIGHT_WEB_SERVER_COMMAND || WEB_SERVER_COMMAND,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
});
