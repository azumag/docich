# 衝立将棋 Cloudflare Webhook Bot prototype

`/webhook` に届くTsuitate Bot向けJSON POSTを検証し、観測できた局面からCSA形式の指し手を返すCloudflare Workersプロトタイプです。既存のオフライン基礎 [`docs/tsuitate-protocol.md`](../../docs/tsuitate-protocol.md) と `src/docich/tsuitate_protocol.py` は変更せず、独立したWorkerとして配置しています。

## 対応範囲

- `ダーク`、`ついたて`、`ついたて5五`、`ついたてリレー` を受け付けます。リレーの状態は `gameId + color + number` ごとに分離します。
- 初回は手数0から `ply` まで、差分は `basePly + 1` から `ply` までを連番検証して保存します。差分の `basePly` は保持済みの最後の手数と完全一致する必要があります。
- Durable Objectのトランザクションで局面履歴、進行位置、直近の指し手、requestIdの応答レシートを一括更新します。同じrequestIdと同じraw本文なら同じ応答を返し、本文が変わっていれば `409` を返します。
- HMAC-SHA256はJSON parseより先に受信raw bytesへ検証します。`X-Tsuitate-Timestamp` の差が300秒以上、Bot ID、署名、`x-amz-content-sha256` が合わないリクエストは拒否します。本文・署名・secretをログへ出しません。
- 状態Worker呼び出しは2.5秒で打ち切り、10秒の対局応答枠に余裕を残します。タイムアウト後に再送された同一リクエストは、DO側の保存済みレシートで処理されます。

指し手は公開された自駒とSFENだけから決定的に選びます。王の移動と長距離駒の遠方移動は候補にせず、隠れた相手駒・王手・ピン・千日手を推定しません。それでも隠し盤面では経路上の駒や王手回避を完全には検証できないため、CSA形式の出力や合法手を保証する将棋エンジンではありません。候補を作れない場合は `422 no_observed_move` で失敗を明示します。

SQLite-backed Durable Objectを保存先に使います。D1や外部DBは使いません。局面は1手ごとのキー、requestIdレシートは対局中保持し、現在は自動削除しません。公開運用前に対局終了の識別と保存期間・容量上限を決める必要があります。

## ローカル検証と起動

Node.js 20以降を用意します。fixtureテストには外部アカウント、ネットワーク、Cloudflareリソースは必要ありません。

```sh
cd workers/tsuitate-bot
npm test
```

ローカルWorkerを起動する場合はWranglerをインストールし、`wrangler.toml` の `BOT_ID` を手元のBot IDへ置き換えます。`.dev.vars` を作成し、ローカル用の `WEBHOOK_SECRET` を自分で設定してから起動してください。

```sh
npm install
# .dev.vars に WEBHOOK_SECRET を設定（このファイルはGit管理外）
npm run dev
```

`wrangler dev` はローカルシミュレーションを使います。`wrangler deploy` はこの手順に含めません。

## Cloudflare設定

- `wrangler.toml` は `GameState` のSQLite Durable Object bindingと初回migrationを記述します。migrationは実デプロイ時に初めてCloudflare側へ適用されます。
- `BOT_ID` はWorker変数、`WEBHOOK_SECRET` はWrangler Secretとして設定します。Secretをソース、ログ、Issue、PRへ書かないでください。
- 実Cloudflareリソースの作成、デプロイ、Secret設定、サイト `https://tsuitateviewer.web.app/` へのBot登録、実対局はまだ行っていません。

将来のSecret設定コマンドは、アカウントと対象Workerを確認したあとに実行してください。

```sh
npx wrangler secret put WEBHOOK_SECRET
```

