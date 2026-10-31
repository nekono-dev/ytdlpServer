import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { pythonCommand } from "../python-command.mjs"

const testsDir = new URL("..", import.meta.url).pathname

export const py = (ids) => {
  const python = pythonCommand()
  const r = spawnSync(python, ["-m", "unittest", ...ids], {
    cwd: testsDir,
    encoding: "utf8",
  })
  if (r.status !== 0 && /ModuleNotFoundError: No module named/.test(r.stderr)) {
    // 依存が無いときは、個々のテストの失敗ではなく原因を1件で示す
    const missing = r.stderr.match(/No module named '([^']+)'/)?.[1]
    assert.fail(
      `テストの依存が不足しています（${python} に ${missing} がありません）。\n` +
        "次のどちらかで依存を入れてください:\n" +
        "  python3 -m venv .venv && .venv/bin/pip install -r apiServer/requirements.txt -r workerServer/requirements.txt -r browserServer/requirements.txt\n" +
        "  （または PYTHON=<依存を入れた python> npm test）",
    )
  }
  assert.equal(r.status, 0, r.stdout + r.stderr)
}
