import { test } from "node:test"
import { py } from "./py.mjs"

const C = "test_cookies.CookiesTest"
const P = "test_cookies.ProfileEntriesTest"

test("cookie-store/AC-1: 保存した cookie の権限は 600 で、置き換えの途中の内容を観測しない", () =>
  py([`${C}.test_save_profile`]))
test("cookie-store/AC-2: 規則に合わないプロファイル名は拒否される", () =>
  py([`${C}.test_validate_profile`]))
test("cookie-store/AC-3: 未保存・有効・失効の状態が判定される", () =>
  py([`${C}.test_profile_state`]))
test("cookie-store/AC-4: 失効中でも cookie を置き直すと有効に戻る", () =>
  py([`${C}.test_profile_state`]))
test("cookie-store/AC-5: 失敗しても更新された cookie が書き戻され、一時ファイルが残らない", () =>
  py([`${C}.test_cookie_file_writeback_and_cleanup`, `${C}.test_cookie_file_writeback_even_on_failure`]))
test("cookie-store/AC-6: 内容が変わらなければ更新時刻が変わらない", () =>
  py([`${C}.test_cookie_file_unchanged_keeps_mtime`]))
test("cookie-store/AC-7: 認証関連のオプションが拒否され、値はエラー文に含まれない", () =>
  py([`${C}.test_check_options`, `${C}.test_error_message_hides_value`]))
test("cookie-store/AC-8: ログ用コマンドで cookie の指定値が伏せられる", () =>
  py([`${C}.test_mask_command`]))
test("cookie-store/AC-9: プロファイル一覧に cookie の中身が含まれない", () =>
  py([`${C}.test_list_profiles_hides_content`]))
test("cookie-store/AC-10: プリセットが先に並び、同名の履歴は現れない", () =>
  py([`${P}.test_presets`, `${P}.test_list_profiles_has_label`]))
test("cookie-store/AC-11: 対象ドメインの配下のホストの URL がプロファイルに解決される", () =>
  py([`${P}.test_resolve_profile`, `${P}.test_history_add_resolve_remove`]))
test("cookie-store/AC-12: 壊れた履歴があってもプリセットの解決が動作する", () =>
  py([`${P}.test_broken_history_is_ignored`]))
test("cookie-store/AC-13: ログイン要求の文言が判定される", () =>
  py([`${C}.test_classify`]))
test("cookie-store/AC-14: 操作 URL の有無でログイン用 URL が組み立てられる", () =>
  py([`${C}.test_login_url`, `${C}.test_login_url_with_start_url`]))
test("cookie-store/AC-15: 3アプリの cookie 処理の内容が一致する", () =>
  py([`${C}.test_api_worker_copies_identical`]))
test("cookie-store/AC-16: エラー文とログ用コマンドに cookie の値が含まれない", () =>
  py([`${C}.test_error_message_hides_value`, `${C}.test_mask_command`]))
