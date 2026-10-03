# 衝立将棋 Cloudflare Webhook Bot prototype

Cloudflare Workersのbuild設定と検証方法は [BUILDS.md](BUILDS.md) にまとめています。GitHub Actionsはbuildとテストを行い、配備は行いません。ローカルCIの成功はCloudflare上の配備・稼働を示しません。

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
npm install
./node_modules/.bin/cf build
npm test
npm run test:workerd
npm run test:bundle
```

`test:workerd`は`wrangler.runtime.toml`に設定した`2026-09-08`をCLIで上書きせずに使い、同じ要求の同時送信、同じrequestIdの別本文競合、storage書込み例外後のSQLite transaction rollback、timeout応答後のlate commit再送を検証します。設定日のruntimeを起動できない場合はテストを失敗させます。テスト状態は一時ディレクトリへ保存して終了時に削除し、Cloudflareアカウントやリソースにはアクセスしません。この設定はローカル専用で、deployしないでください。

本番用Cloudflare設定とtest-onlyの両Wrangler設定は `2026-09-08` を使います。GitHub Actionsは依存関係をインストールし、この日付のままCf build、`test:workerd`、生成bundle検証を実行します。`test:bundle`はCfの実生成設定から日付を読み、overrideせずにbundleを検証します。

Cloudflare CLIでWorkerをローカル起動する場合は、`.dev.vars` にローカル専用の `BOT_ID` と `WEBHOOK_SECRET` を設定し、次を実行します。値はコードへ書かず、このファイルをGitへ追加しないでください。

```sh
npm install
# .dev.vars にローカル専用の値を設定（このファイルはGit管理外）
npm run dev:cf
```

`dev:cf` は `cf dev --local` を使い、`.wrangler/cf-local` へローカル状態を保存します。`cf deploy` はこの手順に含めません。

## Cloudflare設定

- `cloudflare.config.ts` をCfのプロジェクト設定とし、`GameState` のSQLite Durable Object exportと `GAME_STATE` bindingを宣言します。Cf移行時に生成した `wrangler.config.ts` では型生成を無効にしています。旧 `wrangler.toml` はテスト用設定として保持します。
- version preview URLは `worker.previewUrls: false` を明示します。固定版Cfの実生成設定にもfalseが残ることを `test:bundle` で検証します。通常の `workers.dev` 公開URLを無効にする設定ではありません。互換日付は `2026-09-08` です。設定値は `test:workerd` と `test:bundle` がそれぞれruntimeとCf生成物で確認します。[Cf公式設定](https://developers.cloudflare.com/cf/projects/cloudflare-config/)
- `BOT_ID` はplaceholderで、`WEBHOOK_SECRET` は値を含まないSecret binding宣言です。実運用では有効なBot IDと署名Secretが必要です。秘密値をソースやログに出力しないでください。ローカル値はGit管理外の `.dev.vars` に設定します。

この構成のテストはCloudflareアカウントへ接続せず、fixturesとローカルworkerdを使います。Cloudflare上のbuild/deploy checkやruntimeリクエストの成功とは区別してください。

`test:bundle` は先に成功した `cf build` の実生成物を必須入力にします。固定版Cf CLIと同じ版のBuild Output readerでmanifestを読み、bundleから `GameState` とfetch handlerをimportし、SQLite exportと `GAME_STATE` のself-binding、値を持たないSecret宣言を検証します。同版のMiniflare/workerdへmanifestのES modules・compatibility date・DO bindingを渡し、生成bundleを変更せずraw-byte HMAC、署名なし・改竄・bodyhash不一致、古い時刻の拒否、再起動後のreceipt再送、同時要求の競合、差分履歴と反則後の指し手を検証します。テスト用のBot IDと既存fixtureのSecret値だけをローカルbindingへ渡し、外部fetchは拒否します。5分の両側の厳密境界は生成exportをNode.jsで時刻固定して検証し、7秒の受信stream timeoutとcancelも生成fetch handlerをNode.jsで直接呼び出して確認します。

SQLite rollbackと2.5秒のRPC timeout後のlate commitは、生成bundleの `GameState` を継承するメモリ上のtest-only wrapperで故障注入します。実際のSQLite書込み拒否後に局面・session・receiptが残らないこと、再送で成功すること、遅延commitのreceiptが再利用されることを検証し、SQLが利用できることも確認します。このwrapperは生成物を書き換えず、配備しません。従来の4つの `test:workerd` は `wrangler.runtime.toml` のtest-only構成を使い、Cf生成物の検証とは別です。新しいテストではcompatibility dateのoverrideをしません。CfのBuild Output readerはbetaの内部APIなので、Cf CLIを更新する場合はこのテストも再検証してください。
