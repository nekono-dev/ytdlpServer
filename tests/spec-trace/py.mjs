import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"

const testsDir = new URL("..", import.meta.url).pathname

export const py = (ids) => {
  const r = spawnSync(process.env.PYTHON || "python3", ["-m", "unittest", ...ids], {
    cwd: testsDir,
    encoding: "utf8",
  })
  assert.equal(r.status, 0, r.stdout + r.stderr)
}
