import { test } from "node:test"
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"

const py = (name) =>
  spawnSync("python3", ["-m", "unittest", `test_updater_asset.AssetNameTest.${name}`], {
    cwd: new URL("..", import.meta.url).pathname,
    encoding: "utf8",
  })

test("ytdlp-binary/AC-1: glibc で linux 系のバイナリ名が返る", () => {
  const r = py("test_glibc")
  assert.equal(r.status, 0, r.stderr)
})

test("ytdlp-binary/AC-2: musl で musllinux 系のバイナリ名が返る", () => {
  const r = py("test_musl")
  assert.equal(r.status, 0, r.stderr)
})

test("ytdlp-binary/AC-3: 未対応のアーキテクチャで実行時エラーになる", () => {
  const r = py("test_unsupported")
  assert.equal(r.status, 0, r.stderr)
})
