# 衝立将棋 BOT：共通brain・Webhook・beta対局

思考処理を `src/brain/`、サイト固有の変換を `src/adapters/` に分離しています。
既存のCloudflare Webhookに加え、[beta.tsuitate.infoのbot API](https://beta.tsuitate.info/bot-api)
へ接続するNode.jsの反復対局runnerを追加しました。両方が同じbrainとprofile形式を使います。
対局記録、brainだけの改善候補作成、実戦比較の手順は **[ARENA.md](ARENA.md)** を参照してください。

| 責務 | 配置 |
|---|---|
| 共通観測、USI候補、評価関数、profile | `src/brain/index.js` |
| 既存サイトのSFEN・CSA変換 | `src/adapters/webhook.js` |
| betaのPlayerView変換・着手状態管理 | `src/adapters/beta.js` |
| betaの対局・再接続・記録 | `src/arena/`、`scripts/play-beta.mjs` |
| 記録の検証・候補更新・成績比較 | `src/training/`、`scripts/train-brain.mjs` |

Webhookの既定は従来の `observed-sfen-heuristic-v1` です。`BRAIN_PROFILE_JSON` が設定されている
場合だけ、検証済みprofileを**新しい対局**へ適用します。対局の途中や再送でprofileは変わりません。
beta runnerは保存したbrain版が利用できないとき、途中局のcheckpointを保持して停止します。
Webhookは対局profile、brain版、profile hashを席ごとに保存し、配備をまたいでも同じ対局の実際の戦略を記録します。進行中の対局で保存版と実行中Workerのbrain版が異なる場合は `409 brain_version_mismatch` で指し手を返さず、同じ差分を安全に再試行できるようレシートも作りません。
beta runnerは `linear-baseline-v1` を既定とし、`--profile` で同じprofile JSONを読みます。

Cloudflare Workersのbuild設定と検証方法は [BUILDS.md](BUILDS.md) にまとめています。GitHub Actionsはbuildとテストを行い、配備は行いません。ローカルCIの成功はCloudflare上の配備・稼働を示しません。

`/webhook` に届くTsuitate Bot向けJSON POSTを検証し、観測できた局面からCSA形式の指し手を返すCloudflare Workersプロトタイプです。既存のPythonオフライン基礎 [`docs/tsuitate-protocol.md`](../../docs/tsuitate-protocol.md) と `src/docich/tsuitate_protocol.py` は保持しています。Node.jsのbeta adapterは同じ安全契約を回帰テストで固定しています。

## 対応範囲

- 現在受け付けるのは通常の `ついたて` です。`ダーク`、`ついたて5五`、`ついたてリレー` は、モード固有ルールの根拠と検証fixtureが揃うまで `422 unsupported_game_type` で安全に拒否します。
- 初回は手数0から `ply` まで、差分は `basePly + 1` から `ply` までを連番検証して保存します。差分の `basePly` は保持済みの最後の手数と完全一致する必要があります。
- Durable Objectのトランザクションで局面履歴、進行位置、直近の指し手、requestIdの応答レシートを一括更新します。同じrequestIdと同じraw本文なら同じ応答を返し、本文が変わっていれば `409` を返します。
- 任意の `lastCapture` は、Webhook仕様のCSA駒種（例: `FU`）と既存Rustエンジンのwire表現である大文字一文字のSFEN/USI駒種（`P`、`L`、`N`、`S`、`G`、`B`、`R`、`K`）を受け付けます。どちらも固定した駒種コードだけに限定し、省略または空文字列は捕獲なしとして正規化後に省きます。
- HMAC-SHA256はJSON parseより先に受信raw bytesへ検証します。`X-Tsuitate-Bot-Id` は1〜64文字のASCII IDとして形式検証し、`X-Tsuitate-Timestamp` の差が300秒以上、署名、`x-amz-content-sha256` が合わないリクエストは拒否します。受信IDを固定の設定値と照合しません。本文・署名・secret・Bot IDをログへ出しません。
- 本文は受信ストリームの段階で256 KiBに制限し、リクエスト全体は7秒で打ち切ります。状態Worker呼び出しは2.5秒で打ち切り、10秒の対局応答枠に余裕を残します。タイムアウト後に再送された同一リクエストは、DO側の保存済みレシートで処理されます。

既定のlegacy profileは公開された自駒とSFENだけから従来どおり決定的に選びます。王の移動と長距離駒の遠方移動は候補にせず、隠れた相手駒・王手・ピン・千日手を推定しません。linear profileでは王、長距離移動、持駒打ち、任意成りも候補にします。どちらも相手の非公開盤面を知らないため、完全な合法性はサーバーの審判が判定します。候補を作れない場合は `422 no_observed_move` で失敗を明示します。

SQLite-backed Durable Objectを保存先に使います。D1や外部DBは使いません。局面は1手ごとのキー、requestIdレシートは対局中保持し、現在は自動削除しません。`/webhook` の `game_end` は署名済みの `{type,gameId,param,result,winner}` だけを受け付けます。終局通知とBot IDごとの重複・競合状態を同じDO transactionへ書き、commit後だけ空bodyの `204` を返します。記録にはサイト、実際のbrain版・profile hash、自己色・seat、結果、受信時刻、`param` の原文を含み、局面はそのBotの検証済み履歴を参照します。

終局記録は通常の学習データと分離したoffline-only領域です。未照合Bot、複数seat、途中参加、履歴欠落、未知brain版、終局競合は分類して `trainingEligible: false` を維持します。`/offline-review` は同じraw-body HMACとBot IDで認証するread-onlyのPOST exportで、`{type:"offline_review_export",gameId,fromPly,limit}` を受け、`param` と最大100手ずつの可視局面を返します。`param` はopaqueな文字列として保存・返却し、解析・実行・ログ出力しません。livebrainや学習candidateへ自動で混ぜる経路はありません。記録の保持期間と容量上限はまだ設定していません。

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

`test:workerd`は`wrangler.runtime.toml`に設定した`2026-09-08`をCLIで上書きせずに使い、同じ要求の同時送信、同じrequestIdの別本文競合、storage書込み例外後のSQLite transaction rollback、timeout応答後のlate commit再送、終局記録と署名付きoffline exportを検証します。設定日のruntimeを起動できない場合はテストを失敗させます。テスト状態は一時ディレクトリへ保存して終了時に削除し、Cloudflareアカウントやリソースにはアクセスしません。この設定はローカル専用で、deployしないでください。

本番用Cloudflare設定とtest-onlyの両Wrangler設定は `2026-09-08` を使います。GitHub Actionsは依存関係をインストールし、この日付のままCf build、`test:workerd`、生成bundle検証を実行します。`test:bundle`はCfの実生成設定から日付を読み、overrideせずにbundleを検証します。

Cloudflare CLIでWorkerをローカル起動する場合は、`.dev.vars` にローカル専用の `WEBHOOK_SECRET` を設定し、次を実行します。`BOT_ID` の既存設定は残していますが、runtimeでは照合に使用しません。値はコードへ書かず、このファイルをGitへ追加しないでください。

```sh
npm install
# .dev.vars にローカル専用の値を設定（このファイルはGit管理外）
npm run dev:cf
```

`dev:cf` は `cf dev --local` を使い、`.wrangler/cf-local` へローカル状態を保存します。`cf deploy` はこの手順に含めません。

## Cloudflare設定

- `cloudflare.config.ts` をCfのプロジェクト設定とし、`GameState` のSQLite Durable Object exportと `GAME_STATE` bindingを宣言します。Cf移行時に生成した `wrangler.config.ts` では型生成を無効にしています。旧 `wrangler.toml` はテスト用設定として保持します。
- Cf beta.12の変換器は同一Workerを参照するDOにも`script_name`を付け、Dashboardの同一Worker表記とのstrict比較で競合します。`npm install`の`postinstall`と`npm run build:cf`の事前処理が、固定版`@cloudflare/config@0.23.0`のDO変換だけに互換パッチを適用します。参照先が設定のWorker名と等しい場合だけ`script_name`を省略し、外部Workerの参照とstrict判定は維持します。パッケージの版と配布ソースのSHA-256が想定と違う場合は停止します。依存更新時はこのパッチと回帰を再検証してください。npmのinstall scriptsを有効にする必要があります。 remote-config生成器のDO変換にも限定パッチを適用し、APIにない`script_name`・`environment`をown `undefined`キーとして追加しないようにします。明示された外部Worker名・environmentの値は維持します。
- version preview URLは `worker.previewUrls: false` を明示します。固定版Cfの実生成設定にもfalseが残ることを `test:bundle` で検証します。通常の `workers.dev` 公開URLを無効にする設定ではありません。互換日付は `2026-09-08` です。設定値は `test:workerd` と `test:bundle` がそれぞれruntimeとCf生成物で確認します。[Cf公式設定](https://developers.cloudflare.com/cf/projects/cloudflare-config/)
- `observability.logs` はWorkers Logsへの永続化を有効にし、`/webhook` と `/offline-review` の応答についてallowlist済みの構造化イベントを1件出力します。イベントにはHTTP status、固定error code、elapsed、Worker version ID、strategy version、検証済みの通常局面と返したCSA手を必要に応じて含めます。終局・reviewイベントにはevent種別と固定statusだけを記録し、game ID、Bot ID、本文、署名、`param`、プレイヤー名、IP、任意のraw errorは記録しません。相手のlastMoveは規定のmask表現だけを記録します。`invalid_position`の場合は固定されたvalidation stage、局面番号、失敗値の型分類だけを追加し、値自体は記録しません。無効な`lastCapture`文字列には`empty_string`、`lowercase_piece_code`、`other_string`の固定分類を加えます。新しいDB、Logpush先、Workerリソースは作成しません。
- Cloudflareの現行Workers Logs料金表ではFree枠は200,000 events/day・3日保持、Paid枠は20 million events/month込み・7日保持で、超過分は課金対象です。2026-12-01から料金体系がCloudflare Observability pricingへ移行予定です。1 webhookにつき1イベントを保存する設定のため、実際のアカウント利用量は配備後に確認してください。[料金と保持期間](https://developers.cloudflare.com/workers/observability/logs/workers-logs/)
- production設定の `BOT_ID` は既存値 `DoCiAI`、test-only runtimeの `wrangler.runtime.toml` はfixture ID `fixture-bot-id` を維持します。いずれも既存設定との互換性のため残し、runtimeは固定値との一致を要求せず、受信headerのID形式だけを検証します。Durable Objectは従来どおり `gameId` で識別し、保存済み対局履歴・request receiptへ到達できるよう名前空間を変更しません。`WEBHOOK_SECRET` は値を含まないSecret binding宣言です。秘密値をソースやログに出力しないでください。ローカル値はGit管理外の `.dev.vars` に設定します。

この構成のテストはCloudflareアカウントへ接続せず、fixturesとローカルworkerdを使います。PR時点でCloudflareリソースの作成、配備、secret設定、サイトへのBot登録は行っていません。Cloudflare上のbuild/deploy checkやruntimeリクエストの成功とは区別してください。

`test:bundle` は先に成功した `cf build` の実生成物を必須入力にします。固定版Cf CLIと同じ版のBuild Output readerでmanifestを読み、bundleから `GameState` とfetch handlerをimportし、SQLite exportと `GAME_STATE` のself-binding、値を持たないSecret宣言を検証します。同版のMiniflare/workerdへmanifestのES modules・compatibility date・DO bindingを渡し、生成bundleを変更せずraw-byte HMAC、署名なし・改竄・bodyhash不一致、古い時刻の拒否、再起動後のreceipt再送、同時要求の競合、差分履歴と反則後の指し手を検証します。テスト用のBot IDと既存fixtureのSecret値だけをローカルbindingへ渡し、外部fetchは拒否します。5分の両側の厳密境界は生成exportをNode.jsで時刻固定して検証し、7秒の受信stream timeoutとcancelも生成fetch handlerをNode.jsで直接呼び出して確認します。

SQLite rollbackと2.5秒のRPC timeout後のlate commitは、生成bundleの `GameState` を継承するメモリ上のtest-only wrapperで故障注入します。実際のSQLite書込み拒否後に局面・session・receiptが残らないこと、再送で成功すること、遅延commitのreceiptが再利用されることを検証し、SQLが利用できることも確認します。このwrapperは生成物を書き換えず、配備しません。5項目を検証する `test:workerd` は `wrangler.runtime.toml` のtest-only構成を使い、Cf生成物の検証とは別です。新しいテストではcompatibility dateのoverrideをしません。CfのBuild Output readerはbetaの内部APIなので、Cf CLIを更新する場合はこのテストも再検証してください。
