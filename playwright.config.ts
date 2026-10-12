import { defineConfig } from "@playwright/test"

export default defineConfig({
  testDir: "tests/e2e",
  workers: 1,
  webServer: {
    command: `${process.env.PYTHON ?? "python3"} tests/e2e/fake_server.py`,
    url: "http://127.0.0.1:18080/profiles",
    reuseExistingServer: false,
  },
  use: { baseURL: "http://127.0.0.1:18080" },
})
