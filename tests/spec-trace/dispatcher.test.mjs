import { test } from "node:test"
import { py } from "./py.mjs"

const L = "test_dispatch_loop.LoopTest"
const K = "test_dispatch_loop.DockerBackendTest"
const D = "test_dispatcher.DispatcherTest"
const B = "test_dispatcher.BackendsTest"
const J = "test_worker.JobsTest"

test("dispatcher/AC-1: 対象のジョブが無いとワーカーは起動されず、あると起動される", () =>
  py([`${L}.test_starts_workers_only_when_jobs_exist`]))
test("dispatcher/AC-2: ログイン待ちのジョブは数えられず、cookie の更新後に数えられる", () =>
  py([`${L}.test_login_waiting_jobs_are_not_counted_until_cookie_is_refreshed`, `${J}.test_take_retryable_failed_skips_login_wait`]))
test("dispatcher/AC-3: 通知で起床し、通知が無くても定期スキャンで検知される", () =>
  py([`${L}.test_pubsub_wakes_loop_and_flags_update_check`, `${L}.test_scan_detects_jobs_without_notification`]))
test("dispatcher/AC-4: 起動数は対象の数と空きの小さい方で、空きが0なら起動しない", () =>
  py([`${L}.test_start_count_is_min_of_free_and_pending`, `${J}.test_count_pending_targets_capped`]))
test("dispatcher/AC-5: 実行中のワーカーをラベルの一覧から数え直せる", () =>
  py([`${K}.test_running_workers_are_counted_by_label`]))
test("dispatcher/AC-6: 異常終了が続くと起動の間隔が延び、正常終了で0に戻る", () =>
  py([`${D}.test_backoff_increases_then_resets`, `${D}.test_consume_exits_updates_failure_streak`]))
test("dispatcher/AC-7: リースの無いワーカーの処理中ジョブが失敗回数を消費して回収される", () =>
  py([`${J}.test_reclaim_stale_in_progress_without_lease`, `${L}.test_loop_reclaims_jobs_of_workers_without_lease`]))
test("dispatcher/AC-8: リースが生きているワーカーのジョブは回収されない", () =>
  py([`${J}.test_reclaim_stale_keeps_in_progress_with_live_lease`]))
test("dispatcher/AC-9: 取り出し中の記録は、処理中の記録があれば要素だけを消し、無ければキューの先頭に戻る", () =>
  py([`${J}.test_reclaim_stale_processing_returns_to_queue_head`, `${J}.test_reclaim_stale_processing_duplicate_not_requeued`]))
test("dispatcher/AC-10: 所有者の記録が無い処理中ジョブは指定時間を過ぎたものだけ回収される", () =>
  py([`${J}.test_reclaim_stale_legacy_in_progress_without_worker_id`, `${J}.test_reclaim_stale_legacy_in_progress_not_yet_stale`]))
test("dispatcher/AC-11: 異常終了したワーカーのリースが削除され、直ちに回収される", () =>
  py([`${L}.test_abnormal_exit_releases_lease_and_reclaims_immediately`, `${L}.test_normal_exit_does_not_touch_other_leases`, `${B}.test_process_backend_tracks_running_and_exit_codes`, `${K}.test_exit_events_record_exit_codes_and_wake`]))
test("dispatcher/AC-12: ワーカーのコンテナは同じイメージ・マウント・ネットワークで固定のコマンドで作られる", () =>
  py([`${K}.test_worker_container_shares_image_mounts_and_network_with_fixed_command`]))
test("dispatcher/AC-13: 子プロセスの起動と実行中の数の取得ができる", () =>
  py([`${B}.test_process_backend_tracks_running_and_exit_codes`, `${D}.test_start_one_starts_backend_worker`]))
test("dispatcher/AC-14: 終了時に起動したワーカーすべてに停止が指示される", () =>
  py([`${L}.test_run_stops_started_workers_on_shutdown`, `${B}.test_process_backend_stop_all_terminates`, `${K}.test_stop_all_stops_every_worker`]))
test("dispatcher/AC-15: 新版確認の通知で強制確認の要求が立ち、通常の通知では立たない", () =>
  py([`${L}.test_pubsub_wakes_loop_and_flags_update_check`]))
test("dispatcher/AC-16: 旧名の同時実行数は最大数の別名として読まれ、新名が優先される", () =>
  py([`${D}.test_worker_max_falls_back_to_worker_count`, `${D}.test_worker_max_takes_precedence`]))
test("dispatcher/AC-17: 設定の既定値がスキャン間隔30秒とリースの有効期間60秒である", () =>
  py([`${L}.test_config_defaults`]))
test("dispatcher/AC-18: 起動失敗の間隔が5秒始まりで倍になり上限が60秒である", () =>
  py([`${L}.test_backoff_constants`, `${D}.test_backoff_increases_then_resets`]))
test("dispatcher/AC-19: ジョブの JSON を読む処理が無く、コンテナのコマンドが固定である", () =>
  py([`${L}.test_dispatcher_does_not_read_job_contents`, `${K}.test_worker_container_shares_image_mounts_and_network_with_fixed_command`]))
