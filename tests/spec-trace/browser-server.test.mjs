import { test } from "node:test"
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { py } from "./py.mjs"

const root = new URL("../..", import.meta.url).pathname
const A = "test_browser.ApiTest"
const S = "test_browser.SessionTest"
const I = "test_browser.InputTest"
const L = "test_browser.LaunchTest"
const N = "test_browser.NetscapeTest"
const T = "test_browser.TabsTest"

test("browser-server/AC-1: 操作ページが取得でき、開始から保存までの操作で cookie が保存される", () =>
  py([`${A}.test_index_page`, `${A}.test_full_flow`]))
test("browser-server/AC-2: 実行中の開始要求は 409 になり、待機中はブラウザが起動していない", () =>
  py([`${S}.test_only_one_session`, `${A}.test_errors`]))
test("browser-server/AC-3: 保存では対象ドメインの cookie だけが Netscape 形式で保存される", () =>
  py([`${S}.test_commit_saves_only_site_cookies`, `${N}.test_filter_keeps_only_site`, `${N}.test_to_netscape`]))
test("browser-server/AC-4: 対象の cookie が0件の保存は 422 になり、ブラウザは実行中のまま残る", () =>
  py([`${S}.test_commit_without_cookies_keeps_browser`]))
test("browser-server/AC-5: 保存の失敗・取り消し・時間切れ・起動失敗でブラウザが終了する", () =>
  py([`${S}.test_commit_error_tears_down`, `${S}.test_cancel_saves_nothing`, `${S}.test_timeout_tears_down`, `${S}.test_start_failure_returns_to_idle`]))
test("browser-server/AC-6: 入力がブラウザ操作の列に変換される", () =>
  py([`${I}.test_touch`, `${I}.test_mouse`, `${I}.test_keys_and_text`, `${I}.test_ascii_text_uses_key_events`, `${I}.test_probe_and_nav_and_invalid`, `${A}.test_websocket`]))
test("browser-server/AC-7: 表示領域の変更が全タブと画面配信に反映され、範囲外は上下限に収まる", () =>
  py([`${T}.test_viewport_applies_to_all_tabs_and_is_clamped`, `${T}.test_screencast_frame_is_forwarded_and_acked`]))
test("browser-server/AC-8: 新しいタブへ追従し、閉じたときは残りのタブへ戻る", () =>
  py([`${T}.test_popup_tab_is_followed`, `${T}.test_closed_tab_falls_back_to_remaining`]))
test("browser-server/AC-9: ヘッドレスでなく、スマートフォン操作のときだけユーザーエージェントを持つ", () =>
  py([`${L}.test_chromium_args`, `${L}.test_mobile_ua_only_when_mobile`]))
test("browser-server/AC-10: タイムゾーンが UTC のままなら警告が出る", () =>
  py([`${L}.test_utc_warning`]))
test("browser-server/AC-11: ブラウザの制御ポートに外部向けのアドレス指定が無い", () =>
  py([`${L}.test_cdp_port_is_loopback_only`]))
test("browser-server/AC-12: プリセットは定義で起動し、新しいサイトは保存成功時だけ履歴に加わる", () =>
  py([`${S}.test_start_validation`, `${S}.test_preset_uses_definition`, `${S}.test_new_site_added_to_history_only_on_save`]))
test("browser-server/AC-13: 履歴の削除で cookie も消え、プリセットと実行中のプロファイルは拒否される", () =>
  py([`${A}.test_profiles`, `${A}.test_cannot_delete_profile_in_use`]))
test("browser-server/AC-14: クエリでプロファイルが選択され、保存状況が表示される（実ブラウザ）", (t) => {
  const r = spawnSync("npx", ["playwright", "test"], { cwd: root, encoding: "utf8", env: process.env })
  if (r.status === null) return t.skip("playwright を実行できません")
  assert.equal(r.status, 0, r.stdout + r.stderr)
})
test("browser-server/AC-15: プロファイル一覧に保存日時が含まれ、未保存は空になる", () =>
  py([`${A}.test_profiles`]))
test("browser-server/AC-16: 時間切れを待つとブラウザが終了して待機状態に戻る", () =>
  py([`${S}.test_timeout_tears_down`]))
test("browser-server/AC-17: 応答とプロファイル一覧に cookie の値が含まれない", () =>
  py([`${A}.test_full_flow`, `${A}.test_profiles`]))
test("browser-server/AC-18: 待機中はブラウザを生成せず、終了後は全て停止している", () =>
  py([`${S}.test_no_browser_while_idle_and_all_stopped_after`]))
