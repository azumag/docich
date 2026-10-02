# 衝立将棋 Cloudflare Webhook Bot prototype

`/webhook` に届くTsuitate Bot向けJSON POSTを検証し、観測できた局面からCSA形式の指し手を返すCloudflare Workersプロトタイプです。既存のオフライン基礎 [`docs/tsuitate-protocol.md`](../../docs/tsuitate-protocol.md) と `src/docich/tsuitate_protocol.py` は変更せず、独立したWorkerとして配置しています。

## 対応範囲

- 現在受け付けるのは通常の `ついたて` です。`ダーク`、`ついたて5五`、`ついたてリレー` は、モード固有ルールの根拠と検証fixtureが揃うまで `422 unsupported_game_type` で安全に拒否します。
- 初回は手数0から `ply` まで、差分は `basePly + 1` から `ply` までを連番検証して保存します。差分の `basePly` は保持済みの最後の手数と完全一致する必要があります。
- Durable Objectのトランザクションで局面履歴、進行位置、直近の指し手、requestIdの応答レシートを一括更新します。同じrequestIdと同じraw本文なら同じ応答を返し、本文が変わっていれば `409` を返します。
- HMAC-SHA256はJSON parseより先に受信raw bytesへ検証します。`X-Tsuitate-Timestamp` の差が300秒以上、Bot ID、署名、`x-amz-content-sha256` が合わないリクエストは拒否します。本文・署名・secretをログへ出しません。
- 本文は受信ストリームの段階で256 KiBに制限し、リクエスト全体は7秒で打ち切ります。状態Worker呼び出しは2.5秒で打ち切り、10秒の対局応答枠に余裕を残します。タイムアウト後に再送された同一リクエストは、DO側の保存済みレシートで処理されます。

指し手は公開された自駒とSFENだけから決定的に選びます。王の移動と長距離駒の遠方移動は候補にせず、隠れた相手駒・王手・ピン・千日手を推定しません。それでも隠し盤面では経路上の駒や王手回避を完全には検証できないため、CSA形式の出力や合法手を保証する将棋エンジンではありません。候補を作れない場合は `422 no_observed_move` で失敗を明示します。

SQLite-backed Durable Objectを保存先に使います。D1や外部DBは使いません。局面は1手ごとのキー、requestIdレシートは対局中保持し、現在は自動削除しません。公開運用前に対局終了の識別と保存期間・容量上限を決める必要があります。

## ローカル検証と起動

Node.js 22.18以降、npm、Cloudflare CLI `cf` 1.0.0-beta.12を用意します。依存関係には `cf` と、CfのWorker build/runtime要件を満たすWrangler 4.136以降を宣言しています。`npm test`はNodeの`node:test`、`cf/config`の静的設定、Durable Objectの`MemoryStorage` mockを使います。mockは値をstagingしてcallback成功後にcommitし、transactionを直列化しますが、rollbackを検証するfault-injectionテストはありません。

```sh
cd workers/tsuitate-bot
npm test
npm run test:workerd
```

`test:workerd`は`wrangler.runtime.toml`のtest-only Workerを`wrangler dev --local`で起動し、同じ要求の同時送信、同じrequestIdの別本文競合、storage書込み例外後のSQLite transaction rollback、timeout応答後のlate commit再送を検証します。runtimeが設定compatibility dateに未対応なら、起動エラーに表示された最新対応日へテスト実行中だけ上書きし、その日付を出力します。テスト状態は一時ディレクトリへ保存して終了時に削除し、Cloudflareアカウントやリソースにはアクセスしません。この設定はローカル専用で、deployしないでください。

この検証環境のグローバルWrangler 4.119.0/workerdは設定日付`2026-09-21`を拒否し、対応可能な最新日として`2026-08-08`を返しました。ローカルではその日付へoverrideして4つのfixtureが成功しています。一方、GitHub Actionsは通常のnpm installで得たWranglerを使い、overrideなしで設定日付`2026-09-21`のまま4つすべて成功しました。ローカルにあるruntimeとnpm取得版の差はこのように確認できましたが、本番Cloudflare環境の動作は未検証です。

Cloudflare CLIでWorkerをローカル起動する場合は、`.dev.vars` にローカル専用の `BOT_ID` と `WEBHOOK_SECRET` を自分で設定し、次を実行します。値はコードへ書かず、このファイルをGitへ追加しないでください。

```sh
npm install
# .dev.vars にローカル専用の値を設定（このファイルはGit管理外）
npm run dev:cf
```

`dev:cf` は `cf dev --local` を使い、`.wrangler/cf-local` へローカル状態を保存します。`cf deploy` はこの手順に含めません。

## Cloudflare設定

- `cloudflare.config.ts` をCfの明示的なプロジェクト設定とし、`GameState` のSQLite Durable Object exportと `GAME_STATE` bindingを宣言します。Cf移行時に生成した `wrangler.config.ts` では型生成を無効にしています。旧 `wrangler.toml` はレビュー用に保持しており、Cloudflareリソースへは適用していません。
- `BOT_ID` は差し替え用placeholder、`WEBHOOK_SECRET` は値を含まないSecret binding宣言です。実値をソース、ログ、Issue、PRへ書かないでください。ローカル値はGit管理外の`.dev.vars`、将来の本番Secretは別途ユーザーが設定します。
- 実Cloudflareリソースの作成、デプロイ、Secret設定、サイト `https://tsuitateviewer.web.app/` へのBot登録、実対局はまだ行っていません。

この構成ではCloudflareの実アカウントへ接続せず、fixtureとローカルworkerd統合テストを実行できます。ローカルの `cf build` は、環境のWrangler 4.119.0が必要な4.136.0未満で、npmレジストリも名前解決できず未検証です。一方、PR #1550のコード・設定commit `fda7ff4` は [Cloudflare Worker CI run 37054256586](https://github.com/azumag/docich/actions/runs/37054256586) で依存のインストール、`cf build`、fixture、workerd統合テストが成功しました。このrunのbuildログは `Build complete` を示しますが、生成物はartifactとして保存されていません。`test/cloudflare-config.test.js` は `GameState` のSQLite exportとself `GAME_STATE` bindingの設定形を検証し、Worker entrypointのexportテストも通過していますが、ビルド後bundleそのものは直接確認していません。4つのworkerd統合テストは `wrangler.runtime.toml` のtest-only Worker/configで実行し、Cf buildの生成物は使用しません。CIのbuild成功はCloudflareへのdeployや実アカウント上の動作を示すものではありません。
