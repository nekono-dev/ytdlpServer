# 要件（workerServer）

全体の要件は [../requirements.md](../requirements.md)。

## cookie セッション認証

| ID | 要求 | 全体要求 |
|---|---|---|
| W1 | ジョブに `auth_profile` があれば、その cookie を使って取得する | R1 |
| W2 | 実行時にログインが必要と判明した場合（キュー投入後の失効を含む）、ジョブの失敗理由として区別できるように記録する | R5 |
| W3 | ログインが必要で失敗したジョブは、cookie が更新されるまで自動リトライしない（リトライ回数を消費しない） | R5, R6 |
| W4 | cookie の値・認証情報を、ログ・Redis に出さない | R8 |
| W5 | yt-dlp が更新した cookie を失わない（失敗したジョブでも書き戻す） | R1 |

## cookie セッション認証（Phase 4）

| ID | 要求 | 全体要求 |
|---|---|---|
| W6 | `auth_profile` の無いジョブがログイン要求で失敗したとき、URL に対応するプロファイルがあれば、その再ログイン用 URL を記録する | R10, R13 |

## イベント駆動の worker 起動、yt-dlp の更新、ライフサイクル

全体の要件は [../requirements.md](../requirements.md)。

### dispatcher と worker の起動

| ID | 要求 | 全体要求 |
|---|---|---|
| W7 | dispatcher は、キューのジョブ数と自動リトライの対象数を監視し、対象があるときだけ worker を起動する。対象が無い間は worker を起動しない | E1, E7 |
| W8 | dispatcher は、通知の受信と定期スキャンの両方で、対象の発生を検知する。通知を取りこぼしても、定期スキャンの間隔以内に検知する | E2, E7 |
| W9 | 起動する worker の数は、対象のジョブ数と、最大数（`WORKER_MAX`）から実行中の数を引いた空きの、小さい方にする | E5 |
| W13 | worker は、起動したら 1 件（リトライ対象を優先、次にキュー）を処理して終了する。対象が無ければ待たずに終了する | E1 |
| W14 | dispatcher は、起動済みの worker（自身の再起動前に起動したものを含む）を数え直せる | 非機能 |
| W15 | worker が処理を始めずに異常終了し続けても、dispatcher が起動を繰り返して負荷を上げない（間隔を空ける） | E1 |
| W16 | Docker Compose では、dispatcher に許可するコンテナ操作を、worker の作成・起動・停止・状態確認に限る。worker の作成内容は dispatcher 自身の定義から決め、ジョブの内容を含めない | E8 |
| W17 | Alpine では、worker を dispatcher の子プロセスとして起動する。worker 用の常駐サービスを持たない | E6 |
| W18 | dispatcher の停止時、起動した worker に停止を指示する（W24 のとおり、worker は中断したジョブを再実行の対象に戻す） | E6, E17 |
| W19 | 設定（`WORKER_MAX`、更新・リースの各間隔ほか）を環境変数で変更できる。Alpine では既存の `WORKER_COUNT` を `WORKER_MAX` の別名として引き継ぐ | E5, E6 |

### yt-dlp の更新

| ID | 要求 | 全体要求 |
|---|---|---|
| W10 | yt-dlp の更新は dispatcher だけが行う。worker と apiServer は更新しない | E3, E10 |
| W11 | dispatcher は、`UPDATE_INTERVAL`（既定 6 時間）ごと、および起動時に、新版を確認する。新版があるときだけ取得する。確認の時刻・導入中の版・直近の失敗を Redis に記録する | E3, E4 |
| W12 | 取得した版は、チェックサムの一致と、実行確認（`--version` が期待する版を返す）に通ったときだけ適用する。適用は、実行中のプロセスに影響しない原子的な切替で行う。失敗したときは適用せず、導入済みの版で処理を続け、`UPDATE_RETRY_INTERVAL` 後に再確認する | E4, E11 |
| W29 | 新版の確認の依頼（通知）を受けたとき、前回の確認から `UPDATE_COOLDOWN` が経過していなければ確認しない | E12 |
| W30 | 導入先が空・壊れているときは、イメージ（インストール時）に同梱した版で動く。適用済みの古い版は、直近 `KEEP_VERSIONS` 版を残して削除する | E4 |

### ライフサイクル

| ID | 要求 | 全体要求 |
|---|---|---|
| W20 | キューからの取り出しは、処理中を示す記録（worker ごとのリスト）へ原子的に移す形で行う。ジョブの記録（`in_progress`）を作った後に、その記録を消す。解析できないジョブは、捨てずに退避する | E14 |
| W21 | ジョブの状態遷移と、リトライ対象の取得は、原子的に行う。同じジョブを別の worker が先に取得していたら、取得に失敗する | E16 |
| W22 | worker は、生存を示すリース（TTL 付き）を、起動から終了まで更新し続ける | E15 |
| W23 | dispatcher は、リースが切れた worker の `in_progress` のジョブと処理中の記録を回収し、再実行の対象（`failed`、`error_code=interrupted`）にする。回収は、異常終了の扱いとして `failed_count` を消費する。worker の終了を検知したときは、リースの失効を待たずに回収する | E15, E17 |
| W24 | worker は、停止の指示（SIGTERM・SIGINT）を受けたら、yt-dlp を停止し、ジョブを再実行の対象（`failed`、`error_code=interrupted`）にして終了する。この場合は `failed_count` を消費しない | E17 |
| W25 | worker は、リースを更新できない状態が `LEASE_TTL` 続いたら、自ら実行を中止して終了する。完了・失敗の記録は、そのジョブの所有者が自分であるときだけ行う | E16 |
| W26 | 中断したジョブの再実行は、yt-dlp の再開の機能を妨げない（保存先・ファイル名を変えない） | E18 |
| W27 | 導入前から残る `in_progress`（所有者の記録が無いもの）は、`INPROGRESS_STALE` を過ぎたら回収する | E15 |
| W28 | `interrupted` のジョブは、通常の失敗と同じリトライの対象（`failed_count < RETRY_COUNT`）とし、`/download/retry` の対象にもする | E15, E17 |

### 制約

- dispatcher は yt-dlp・ffmpeg を使わない。待機中の外部への通信は、新版の確認（GitHub）を除いて行わない（Redis と、Compose では docker-socket-proxy のみ）。
