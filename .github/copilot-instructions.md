# ytdlpServer

yt-dlpを非同期で実行することを目的としたサーバーアプリケーション。

# アーキテクチャ

- APIサーバ: ユーザのAPIリクエスを受け、解析する。ytdlpジョブをキューに追加する。
- キューサーバ: APIサーバのキューを格納する。
- ワーカー: キューからジョブを取得し、yt-dlpを実行する。 

# 仕様

## yt-dlpの仕様

yt-dlpでは、以下のコマンドによりURLのコンテンツ情報をJSON形式で取得できる。
```
yt-dlp -j --flat-playlist <URL>
```
上記のstdoutの結果は、URL先のコンテンツによって変化する。

- 単一動画の場合 
  - `formats`フィールドを含む。objectの配列形式。
  - `playlist`フィールドはnull。
- プレイリストの場合
  - Jsonではなく、各行がJSONオブジェクトとなる形式。
  - `playlist`フィールドにStringが含まれる。

いずれの場合も
- `webpage_url`フィールド: 動画のURLを含む
- `title`フィールド: 動画のタイトル
- フィールドの一部は UTF-16 でエンコードされているため、必要に応じてデコードする。

## APIサーバの仕様

ユーザからジョブを受け取る。

### API エンドポイント

- method: POST
- path: /ytdlp
- request body
  - url: string (必須) - ダウンロード対象のURL
  - options: string (任意) - yt-dlpに渡す追加オプション。`--best-video --best-audio`など、複数のオプションが1つの文字列として渡される。
  - savedir: string (任意) - 指定された場合はサブディレクトリを作成し、そこにダウンロードする。

### 動作詳細

送られたリクエストのURLパラメータに対して、以下相当の操作をpythonのytdlpモジュールで実行する。
`--no-playlist`オプションが指定されている場合は削除する。

```
yt-dlp -j --flat-playlist <URL>
```
上記の結果を解析し、ジョブキューを作成する。
- 単一動画の場合
  - 1つのジョブをキューに追加する。
- プレイリストの場合
  - 各行のJSONオブジェクトを解析し、各動画ごとにジョブをキューに追加する。
    - 例: プレイリストに10本の動画が含まれている場合、各動画のキューとして分解し、10個のジョブをキューに追加する。

### APIサーバのレスポンス

- 成功時: HTTP 200 OK
  - body:
    - message: "Jobs added to queue"
- 失敗時: HTTP 4xx/5xx
  - body:
    - message: エラーメッセージ

## キューサーバの仕様

### キューの内容

ジョブは以下の情報を保持する。

ジョブキュー: "ytdlp:queue"
- url: string - ダウンロード対象のURL
- options: string[]  (任意) - APIサーバから渡されたyt-dlpオプションを分解した配列形式。
- savedir: string  (任意)
- filename: string (任意) - ファイル名となる文字列（拡張子を除く）
- id: string - ジョブの一意なID

ジョブステータスキュー: "ytdlp:jobs:<status>:<job_id>"
- job_id: string - ジョブの一意なID
- status: string - ジョブの状態。`pending`, `in_progress`, `completed`, `failed`のいずれか。
- url: string - ダウンロード対象のURL
- options: string[]  (任意) - yt-dlpオプションの配列形式。
- savedir: string  (任意)
- created_at: timestamp - ジョブ
- started_at: timestamp  (任意) - ジョブ開始時刻
- completed_at: timestamp  (任意) - ジョブ完了時刻
- failed_at: timestamp  (任意) - ジョブ失敗時刻
- error: string  (任意) - 失敗時のエラーメッセージ
- failed_count: integer - 失敗回数

ステータスキューのステータスがキーに入っているのは、ジョブ全体の見通しを良くするためである。

## ワーカーの仕様

取得したキューをもとにyt-dlpコマンドを実行する。ワーカーによるダウンロードは同期処理で行われる。
並列処理は実装せず、コンテナを複数起動することで並列処理に対応する。
savedirが指定されている場合は、指定されたサブディレクトリでyt-dlpを実行することで、ダウンロード先を分ける。
