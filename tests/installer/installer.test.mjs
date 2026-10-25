import { test } from "node:test"
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { existsSync, readFileSync } from "node:fs"

const root = new URL("../..", import.meta.url).pathname

const e2eResults = () => {
  const file = process.env.INSTALLER_E2E_RESULT
  if (!file || !existsSync(file)) return null
  const map = new Map()
  for (const line of readFileSync(file, "utf8").split("\n")) {
    const m = line.match(/^(AC-\d+)([a-z]?) (PASS|FAIL)(.*)$/)
    if (m) map.set(m[1] + m[2], { ok: m[3] === "PASS", note: m[4] })
  }
  return map
}

const e2e = (ac) => (t) => {
  const results = e2eResults()
  if (!results) return t.skip("INSTALLER_E2E_RESULT が未指定（LXC での検証結果ファイル）")
  const hits = [...results].filter(([k]) => new RegExp(`^${ac}[a-z]?$`).test(k))
  assert.ok(hits.length > 0, `${ac} の検証結果がありません`)
  for (const [k, v] of hits) assert.ok(v.ok, `${k} が失敗: ${v.note}`)
}

test("installer/AC-1: 空のコンテナで install すると API が応答する", e2e("AC-1"))
test("installer/AC-2: 手編集した値が残り、引数指定の値だけが上書きされる", e2e("AC-2"))
test("installer/AC-3: 宣言のないキーと禁則文字は何も変更せず中止する", e2e("AC-3"))
test("installer/AC-4: 数値以外のワーカー数はパッケージ導入前に中止する", e2e("AC-4"))
test("installer/AC-5: 設定の保存先の配下の動画保存先は中止する", e2e("AC-5"))
test("installer/AC-6: update で新キーが追記され既存値は変わらない", e2e("AC-6"))
test("installer/AC-7: nginx の有効・無効が切り替わる", e2e("AC-7"))
test("installer/AC-8: Redis Insight を導入できない環境で redis-commander が起動する", e2e("AC-8"))
test("installer/AC-9: uninstall は設定・cookie・動画を残す", e2e("AC-9"))
test("installer/AC-10: purge は動画を残して設定・cookie・Redis のデータ・専用ユーザーを消す", e2e("AC-10"))
test("installer/AC-11: status が5項目または未導入を表示する", e2e("AC-11"))
test("installer/AC-12: Redis と pot-provider は 127.0.0.1 限定で API は自動再起動する", e2e("AC-12"))
test("installer/AC-14: 埋め込みと異なるコミットでは中止する", e2e("AC-14"))
test("installer/AC-15: 再実行は再ビルドせず 120 秒以内に終わる", e2e("AC-15"))
test("installer/AC-16: 設定ファイル 640（グループ ytdlpserver）、cookie の保存先 700", e2e("AC-16"))
test("installer/AC-18: 標準構成の install が 900 秒以内に終わる", e2e("AC-18"))
test("installer/AC-19: 各サービスは専用ユーザーで動き、動画の保存先はそのユーザーが所有する", e2e("AC-19"))

test("installer/AC-13: 公開用ワークフローが3つのトリガーと検査・生成・公開のジョブを持つ", () => {
  const yml = readFileSync(`${root}/.github/workflows/release-installer.yml`, "utf8")
  for (const needle of [
    "push:",
    "workflow_dispatch:",
    "tags: [\"v*\"]",
    "  check:",
    "./.github/workflows/installer-check.yml",
    "  build:",
    "  release-tag:",
    "upload-artifact",
    "gh release upload",
  ]) {
    assert.ok(yml.includes(needle), `${needle} がありません`)
  }
})

test("installer/AC-17: shellcheck の警告が 0 件である", (t) => {
  const r = spawnSync("sh", ["-c", "shellcheck -S warning installer/*.sh installer/run/*.sh tests/installer/e2e.sh"], {
    cwd: root,
    encoding: "utf8",
  })
  if (r.status === 127) return t.skip("shellcheck がありません")
  assert.equal(r.status, 0, r.stdout + r.stderr)
})
