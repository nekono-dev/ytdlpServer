import { test } from "node:test"
import { py } from "./py.mjs"

const A = "test_api.ApiTest"

test("api-server/AC-1: 解析に成功した要求でジョブが積まれ、200 が返る", () =>
  py([`${A}.test_download_notifies_dispatcher`]))
test("api-server/AC-2: 同じ URL を2回投入するとジョブ ID の異なる2件が積まれる", () =>
  py([`${A}.test_same_url_twice_gets_distinct_job_ids`]))
test("api-server/AC-3: 予約の保存と予約実行でジョブが積まれる", () =>
  py([`${A}.test_schedule_keeps_profile_and_requeues_on_login_required`]))
test("api-server/AC-4: プロファイル名がジョブと予約に含まれる", () =>
  py([`${A}.test_profile_passed_to_job_and_queue`]))
test("api-server/AC-5: 認証関連のオプションと不正なプロファイル名は 400 になる", () =>
  py([`${A}.test_rejects_forbidden_options_and_bad_profile`]))
test("api-server/AC-6: 失効中のプロファイルは解析せずに 401 になり、置き直すと解析に進む", () =>
  py([`${A}.test_expired_profile_short_circuits_then_revalidates`]))
test("api-server/AC-7: 解析のログイン要求で 401 になり、プロファイルが失効として記録される", () =>
  py([`${A}.test_login_required_marks_profile_expired`]))
test("api-server/AC-8: プロファイル未指定のログイン要求が再ログイン用 URL つきの 401 になる", () =>
  py([`${A}.test_login_required_without_profile`, `${A}.test_login_url_in_401`]))
test("api-server/AC-9: 未保存のプロファイルの指定は未登録を示す 401 になる", () =>
  py([`${A}.test_unknown_profile`]))
test("api-server/AC-10: 有効な cookie のある対応プロファイルで解析される", () =>
  py([`${A}.test_auto_select_valid_profile`]))
test("api-server/AC-11: 未保存・失効中の対応プロファイルは cookie なしで解析され、候補が案内される", () =>
  py([`${A}.test_auto_select_missing_or_expired_probes_without_cookie`, `${A}.test_auto_select_login_required_reasons`]))
test("api-server/AC-12: 予約実行のログイン要求で予約が先頭に戻る", () =>
  py([`${A}.test_schedule_keeps_profile_and_requeues_on_login_required`]))
test("api-server/AC-13: 通知が発行され、通知の失敗でも受理が返る", () =>
  py([`${A}.test_download_notifies_dispatcher`, `${A}.test_notify_failure_does_not_fail_request`]))
test("api-server/AC-14: ログイン要求以外の解析失敗で 400 と新版確認の通知が返る", () =>
  py([`${A}.test_other_probe_failure_requests_update_check_without_restart`]))
test("api-server/AC-15: 失敗した予約が退避され、後続の予約が処理され、応答に件数が含まれる", () =>
  py([`${A}.test_failed_scheduled_request_is_moved_aside_and_rest_continues`]))
test("api-server/AC-16: 再実行要求で失敗回数が 0 になり通知が発行される", () =>
  py([`${A}.test_retry_resets_failed_count_and_notifies`]))
test("api-server/AC-17: 再実行要求の途中で取得されたジョブは再作成されない", () =>
  py([`${A}.test_retry_does_not_resurrect_key_taken_by_worker`]))
test("api-server/AC-18: プロファイル一覧に再ログイン用 URL があり、cookie の中身が無い", () =>
  py([`${A}.test_profiles_endpoint_login_url`, `${A}.test_profiles_endpoint_hides_content`]))
test("api-server/AC-19: 解析の失敗後にプロセスを終了させる処理が呼ばれない", () =>
  py([`${A}.test_other_probe_failure_requests_update_check_without_restart`]))
test("api-server/AC-20: 拒否応答にオプション名だけが含まれ、値は含まれない", () =>
  py([`${A}.test_rejects_forbidden_options_and_bad_profile`, "test_cookies.CookiesTest.test_error_message_hides_value"]))
