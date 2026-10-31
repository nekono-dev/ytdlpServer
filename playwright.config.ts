import { defineConfig } from "@playwright/test"
import { pythonCommand } from "./tests/python-command.mjs"

export default defineConfig({
  testDir: "tests/e2e",
  workers: 1,
  webServer: {
    command: `${pythonCommand()} tests/e2e/fake_server.py`,
    url: "http://127.0.0.1:18080/profiles",
    reuseExistingServer: false,
  },
  use: { baseURL: "http://127.0.0.1:18080" },
})
