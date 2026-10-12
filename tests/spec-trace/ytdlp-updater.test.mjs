import { test } from "node:test"
import { py } from "./py.mjs"

const U = "test_updater.UpdateTest"
const D = "test_dispatcher.UpdaterTest"
const A = "test_updater_asset.AssetNameTest"

test("ytdlp-updater/AC-1: 間隔が経過していないと確認せず、経過していると確認して取得する", () =>
  py([`${D}.test_check_and_update_skips_within_interval`, `${D}.test_check_and_update_checks_when_due`]))
test("ytdlp-updater/AC-2: 最新の版が導入中の版と同じなら取得しない", () =>
  py([`${D}.test_check_and_update_no_op_when_already_current`]))
test("ytdlp-updater/AC-3: チェックサムまたは版が一致しない取得物は適用されない", () =>
  py([`${D}.test_verify_checksum`, `${U}.test_checksum_or_version_mismatch_is_not_applied`]))
test("ytdlp-updater/AC-4: 適用後は現在の版へのリンクが新しい版を指す", () =>
  py([`${U}.test_apply_switches_current_and_prunes_old_versions`]))
test("ytdlp-updater/AC-5: 確認が失敗しても導入済みの版は変わらず、失敗が記録される", () =>
  py([`${D}.test_check_and_update_records_error_on_failure`, `${U}.test_failure_keeps_current_and_uses_retry_interval`]))
test("ytdlp-updater/AC-6: 失敗の後は短い再確認の間隔で再確認される", () =>
  py([`${U}.test_failure_keeps_current_and_uses_retry_interval`]))
test("ytdlp-updater/AC-7: 最短間隔が経過していない依頼では確認が行われない", () =>
  py([`${D}.test_force_respects_cooldown`]))
test("ytdlp-updater/AC-8: 確認後に確認の時刻と導入中の版が記録される", () =>
  py([`${U}.test_successful_update_records_state`]))
test("ytdlp-updater/AC-9: 指定数を超える古い版が削除され、現在の版は残る", () =>
  py([`${U}.test_apply_switches_current_and_prunes_old_versions`]))
test("ytdlp-updater/AC-10: libc とアーキテクチャに対応するバイナリ名が選ばれる", () =>
  py([`${A}.test_glibc`, `${A}.test_musl`]))
test("ytdlp-updater/AC-11: 未対応のアーキテクチャでは実行時エラーになる", () =>
  py([`${A}.test_unsupported`]))
test("ytdlp-updater/AC-12: 検索パスは導入先の現在の版を先に、同梱の版を後に並べる", () =>
  py([`${U}.test_search_path_prefers_install_dir_over_bundled_version`]))
test("ytdlp-updater/AC-13: 現在の版があれば取得せず、取得の失敗は呼び出し元へ伝わる", () =>
  py([`${U}.test_initial_version_only_when_empty_and_failure_propagates`]))
test("ytdlp-updater/AC-14: 環境変数の指定が設定に反映され、未指定は既定値になる", () =>
  py([`${U}.test_config_from_env_and_defaults`]))
test("ytdlp-updater/AC-15: 確認は github.com への HEAD 要求だけで、pypi.org を参照しない", () =>
  py([`${U}.test_check_is_one_head_request_to_github_only`]))
test("ytdlp-updater/AC-16: 設定の既定値が 21600 秒と 1800 秒である", () =>
  py([`${U}.test_config_from_env_and_defaults`]))
