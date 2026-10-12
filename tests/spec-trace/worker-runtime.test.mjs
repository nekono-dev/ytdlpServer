import { test } from "node:test"
import { py } from "./py.mjs"

const J = "test_worker.JobsTest"
const F = "test_worker.FunctionTest"
const R = "test_worker.RunOnceTest"
const M = "test_worker.MainRecordFailureTest"
const X = "test_worker_runtime.RuntimeTest"

test("worker-runtime/AC-1: ジョブが無いと待たずに終了し、リトライ対象はキューより先に処理される", () =>
  py([`${R}.test_no_job_returns_immediately`, `${R}.test_retries_failed_job_before_queue`, `${R}.test_completed_job_from_queue`]))
test("worker-runtime/AC-2: 同じリトライ対象を取得できるのは1つのワーカーだけで、上限超過とログイン待ちは対象外になる", () =>
  py([`${J}.test_take_retryable_failed_claims_ownership_once`, `${J}.test_take_retryable_failed_skips_exhausted`, `${J}.test_take_retryable_failed_skips_login_wait`]))
test("worker-runtime/AC-3: キューの取り出しは処理中の記録を経由し、空のキューでは何も返らない", () =>
  py([`${J}.test_take_from_queue_then_commit`, `${J}.test_take_from_queue_empty_returns_none`]))
test("worker-runtime/AC-4: 解析できないジョブは退避用のキューへ移り、終了コード 0 で終了する", () =>
  py([`${J}.test_discard_processing_moves_to_dead_queue`, `${R}.test_dead_letter_on_invalid_json`]))
test("worker-runtime/AC-5: リースの取得・更新・解放ができ、解放後は生存と判定されない", () =>
  py([`${J}.test_lease_lifecycle`]))
test("worker-runtime/AC-6: 所有者が別のワーカーなら結果が書き込まれない", () =>
  py([`${J}.test_is_owner`, `${R}.test_lost_ownership_does_not_record_result`]))
test("worker-runtime/AC-7: 停止の指示の後は実行せず再実行の対象になり、失敗回数が増えない", () =>
  py([`${J}.test_mark_interrupted_no_consume`, `${R}.test_stop_before_execution_marks_interrupted_without_consuming`]))
test("worker-runtime/AC-8: 実行中の停止の指示で yt-dlp に停止が送られ、猶予を過ぎると強制終了される", () =>
  py([`${X}.test_stop_request_terminates_ytdlp_and_kills_after_grace`]))
test("worker-runtime/AC-9: リースの更新が失敗し続けると実行が中止される", () =>
  py([`${X}.test_heartbeat_failure_for_lease_ttl_stops_the_job`]))
test("worker-runtime/AC-10: 保存先とファイル名が出力先に反映され、空ならジョブの識別子が使われる", () =>
  py([`${X}.test_output_template_uses_savedir_filename_or_job_id`]))
test("worker-runtime/AC-11: 失敗したジョブを再実行しても保存先とファイル名が変わらない", () =>
  py([`${X}.test_failed_job_is_retried_with_same_destination`, `${X}.test_output_template_uses_savedir_filename_or_job_id`]))
test("worker-runtime/AC-12: 同名のファイルがあると末尾に連番が付き、使用済みなら次の連番になる", () =>
  py([`${X}.test_output_name_gets_numbers_when_destination_has_same_name`]))
test("worker-runtime/AC-13: 同名が無ければ名前は変わらず、拡張子の違うファイルと途中のファイルも同名として扱われる", () =>
  py([`${X}.test_output_name_gets_numbers_when_destination_has_same_name`, `${X}.test_output_name_treats_other_extensions_and_partial_files_as_same_name`]))
test("worker-runtime/AC-14: 選んだ名前が記録され、再実行で同じ名前を使い、別のジョブとは重ならない", () =>
  py([`${X}.test_output_name_is_recorded_stable_and_not_shared_between_jobs`]))
test("worker-runtime/AC-15: YouTube の既定値が加わり、明示した指定は変更されない", () =>
  py([`${X}.test_youtube_defaults_added_and_explicit_choice_kept`]))
test("worker-runtime/AC-16: cookie の一時コピーで実行し、失敗しても書き戻し、ログの値が伏せられる", () =>
  py([`${F}.test_cmd_has_cookies_and_log_is_masked`, `${F}.test_no_profile_no_cookies_flag`, `${F}.test_failure_still_writes_back_cookies`]))
test("worker-runtime/AC-17: 未保存のプロファイルのジョブは実行せずログイン要求として失敗する", () =>
  py([`${F}.test_missing_profile_fails_as_login_required`]))
test("worker-runtime/AC-18: ログイン要求の失敗で理由の区分と再ログイン用 URL が記録され、プロファイルが失効になる", () =>
  py([`${M}.test_record_failure_login_required`, `${M}.test_record_failure_login_url`]))
test("worker-runtime/AC-19: プロファイルの無いジョブでは URL に対応するプロファイルの再ログイン用 URL が記録される", () =>
  py([`${M}.test_record_failure_login_url_resolves_profile`]))
test("worker-runtime/AC-20: ログイン要求以外の失敗で失敗回数が増え、理由の区分が空になる", () =>
  py([`${R}.test_failed_job_increments_failed_count`, `${M}.test_record_failure_other_error_clears_code`]))
test("worker-runtime/AC-21: 停止の猶予の既定値が 20 秒である", () =>
  py([`${X}.test_default_settings`]))
test("worker-runtime/AC-22: リースの更新間隔の既定値が10秒、有効期間の既定値が60秒である", () =>
  py([`${X}.test_default_settings`, `${X}.test_worker_id_comes_from_environment`]))
test("worker-runtime/AC-23: ログ用のコマンドと失敗の記録に cookie の値が含まれない", () =>
  py([`${F}.test_cmd_has_cookies_and_log_is_masked`, `${X}.test_failure_record_never_contains_cookie_values`]))
