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

### beta対局のowner操作

WebUIの「コーナー」に状態更新・1局開始・終了後停止を追加しました。既存operator認証を使い、開始・停止は既存Host/Origin、JSON、CSRF、確認guardを通ります。read-only利用者は状態取得を含め拒否します。WebUI認証設定とloopback/Tailscale ACLが実運用でownerに限定されていることは、このローカル検証では確認していません。

WebUIサーバーは専用HMACで固定 `POST /beta-control` へ `status` / `start` / `stop` / `reconcile` だけを送ります。ブラウザへsecretを渡さず、任意URL・コマンド・対局数は受け付けません。Workerの `BETA_CONTROL_SECRET` とWebUIの `DOCICH_BETA_CONTROL_SECRET` は同じ専用値を参照し、未設定は拒否します。Webhookの `WEBHOOK_SECRET`、サイトの `TSUITATE_BOT_TOKEN`、WebUI operator/viewer tokenは流用しません。raw本文のHMAC-SHA256と300秒未満の時刻差を検証します。bodyは4 KiB・受信1秒、DO呼出し2.5秒、WebUI通信5秒に制限し、secret・署名・本文・rawエラーはログやブラウザへ返しません。

初期状態は `stopped` です。singleton名 `beta:DoCiAI` に対し、**明示runごとに最大1局**を予約します。同じrunIdの再送・並行開始・重複alarmで再募集しません。次の新しいrunIdは前runの終局記録保存、socket終了、alarm削除が済んだ `readyForNextRun=true` の時だけ開始できます。終局後の自動反復はありません。古いrunIdのstart/stopは保存済みreceiptを返し、現在runを再開始・停止しません。paused・不明な状態では次局を開始せず、reset APIもありません。

queue待ちは開始予約から60秒です。退出ACK確認に最大5秒、その後の遅延match通知待機に最大5秒を使います。stopは待機中なら退出し、対局中なら着手を続けて結果保存後に停止します。停止にはこのstop操作を使ってください。UIは曖昧な開始応答の再確認用に、秘密ではないrunIdをsessionStorageに保持します。

SQLiteの `beta:meta` に現在run・対局ID・世代・brain/profile・停止要求、`beta:checkpoint` に自分の観測と未確認着手、`beta:terminal` に最新終局記録を保存します。`beta:record:<runId>` と `beta:run:<runId>` に各runの記録とreceiptを残し、`beta:game:<gameId>` で過去局への混線を拒否します。着手はcheckpoint保存後に送信し、古い世代の書込みを拒否します。値は1 MiB以下で、保存失敗時は以前のcheckpointを保持して停止します。記録は冪等に保存し、矛盾する結果は上書きしません。履歴の自動削除・容量保持方針は未実装です。

20秒間隔のalarmは既知の対局IDだけを復元し `game:sync` します。cold restoreでID未保存なら `unknown_match_state` に停止し、対局がないとは推測せず再募集もしません。復元・通信断を経た対局は学習対象外です。保存済みbrain版が利用できない場合もcheckpointを保持します。配備や障害による切断負けのリスクは残ります。

終局結果は現在gameIdの公開棋譜と照合します。取得は `redirect: "manual"` を使い、3xxを拒否して別URLへ追従しません。`game:end`は結果照会のきっかけとして扱い、その原文から勝敗や隠し盤面を保存しません。空の同期応答が続いても、公開結果の照会は最大5回、間隔1.5秒・各取得5秒の枠を最後まで使います。通知の重複で間隔を短縮しません。結果未確定ならcheckpointを保持して停止し、終了済みPlayerViewを確認できた場合だけ勝敗不明の終局記録を保存します。既にpausedのrunへ自動照会・自動復帰はしません。

ownerが明示する `reconcile` は、同じrunIdの `paused / terminal_unconfirmed`、未保存の同じgameId・世代・checkpointだけを扱います。既存operator APIへ `{"action":"reconcile","runId":"fixture-run","confirm":true}` をPOSTすると、同じHost/Origin/CSRF guardと専用サービスHMACを通ります。画面の開始ボタンはこの操作を兼ねません。公開棋譜を1回・1.5秒で取得し、結果・日時・gameIdを検証します。終局記録、run receipt、checkpoint終了、alarm削除は1つのSQLite transactionで保存し、成功後だけ `readyForNextRun=true` にします。照会中に世代やcheckpointが変わった場合、結果未確定、保存失敗では元のpendingと観測を保持します。再送は保存済み結果を返し、Socket接続・募集・着手はしません。保存元のbrain版・profile・観測・着手履歴を保持し、v2局の回復でも実行中のv3へ置換しません。回復した観測は勝敗確定後も学習対象外です。次の明示startだけが実行中のbrain版を使います。秘密や結果本文を入力へ渡す操作、記録の削除、状態resetはありません。

Socket.IO 4.8.4の公開ブラウザ配布をnative WebSocket transportだけで使います。外向きWebSocketは[DOのhibernation対象外](https://developers.cloudflare.com/durable-objects/best-practices/websockets/)で常駐中はduration課金・quota消費があります。アカウントの現plan・残量は未確認で、無料稼働を保証しません。plan変更は行っていません。

`src/worker.js` が既存Webhookと認証controlを束ねます。`cloudflare.config.ts` はBetaArenaのSQLite export、`BETA_ARENA` binding、`nodejs_compat`、値なしsecretを宣言します。旧 `wrangler.toml` も同じbindingとmigration宣言を持ちます。旧plain変数の `BETA_ARENA_ENABLED="false"` 宣言は設定差分を作らないためmetadataに残しますが、runtimeは参照しません。buildは実DOを作りません。利用可否の環境変数は参照しません。以前の `BETA_ARENA_ENABLED` / `DOCICH_BETA_CONTROL_ENABLED` が残っていても値は無視し、認証済みの明示startでのみ募集します。Workerには既存の `BETA_CONTROL_SECRET` と `TSUITATE_BOT_TOKEN`、WebUIには同じ共有キーの `DOCICH_BETA_CONTROL_SECRET` と `DOCICH_BETA_CONTROL_URL` が必要です。URLはこのWorker名のHTTPS workers.dev rootだけに限定し、redirect・ambient proxyは使いません。secretやURL未設定・認証不正は引き続き拒否します。

ローカル検証は `npm test` と `npm run test:beta-workerd`、repo rootから `python3 -m pytest -q tests/test_tsuitate_beta_control.py` です。一時SQLite・localhostのWebUI/Engine.IO/Socket.IO・明示fixture値だけでoperator→HMAC→DO、viewer/CSRF拒否、並行再送、手動2回目開始、旧run停止無効、rollback、sync-only復元、終局保存・再起動を確認します。Miniflare v5はinline bundleと `resourcePersistencePath` を使い、再起動前後のDO IDと未確認着手を照合します。

この変更は親の独立レビューとmerge・配備判断を待ちます。main連動のWorkers Buildsがある環境ではmergeも配備に繋がり得ます。既存secretを使い、新しい秘密は不要です。この変更の検証では実DO/D1作成、secret生成・設定、配備、Bot登録、beta接続・実対局を行っていません。

- 現在受け付けるのは通常の `ついたて` です。`ダーク`、`ついたて5五`、`ついたてリレー` は、モード固有ルールの根拠と検証fixtureが揃うまで `422 unsupported_game_type` で安全に拒否します。
- 初回は手数0から `ply` まで、差分は `basePly + 1` から `ply` までを連番検証して保存します。差分の `basePly` は保持済みの最後の手数と完全一致する必要があります。
- Durable Objectのトランザクションで局面履歴、進行位置、直近の指し手、requestIdの応答レシートを一括更新します。同じrequestIdと同じraw本文なら同じ応答を返し、本文が変わっていれば `409` を返します。
- 旧版 `6874345` のreceipt/sessionには所有Bot情報がありません。このstateの再送・差分・再初期化は `409 legacy_identity_unverified` とし、元の履歴とreceiptを保存したまま新しいreceiptも作りません。受信headerから旧所有Botを推測・割当しません。所有情報が検証済みのsessionへの別Bot要求もreceiptを作らず、正しいBotによる同じrequestIdの再送を妨げません。旧stateの所有情報を確認する移行はこのPRの範囲外です。`bc0f1ac` が保存済みの `bot_identity_mismatch` 拒否receiptは、同じ本文・requestIdの再送で現在のsession所有情報を再検証します。成功receiptや本文不一致の保護は維持します。
- 任意の `lastCapture` は、Webhook仕様のCSA駒種（例: `FU`）と既存Rustエンジンのwire表現である大文字一文字のSFEN/USI駒種（`P`、`L`、`N`、`S`、`G`、`B`、`R`、`K`）を受け付けます。どちらも固定した駒種コードだけに限定し、省略または空文字列は捕獲なしとして正規化後に省きます。
- HMAC-SHA256はJSON parseより先に受信raw bytesへ検証します。`X-Tsuitate-Bot-Id` は1〜64文字のASCII IDとして形式検証し、`X-Tsuitate-Timestamp` の差が300秒以上、署名、`x-amz-content-sha256` が合わないリクエストは拒否します。受信IDを固定の設定値と照合しません。本文・署名・secret・Bot IDをログへ出しません。
- 本文は受信ストリームの段階で256 KiBに制限し、リクエスト全体は7秒で打ち切ります。状態Worker呼び出しは2.5秒で打ち切り、10秒の対局応答枠に余裕を残します。タイムアウト後に再送された同一リクエストは、DO側の保存済みレシートで処理されます。

既定のlegacy profileは、公開王手が無い／不明な時には従来どおり王の移動と長距離駒の遠方移動を候補にせず、公開された自駒とSFENから決定的に選びます。brain v2では、公開王手が確定し残り試行予算が2以上（または不明）の時は両profileで玉の移動を先に試し、全て拒否されたら合駒・捕獲等を含む候補へ進みます。公開反則情報から最後の1試行と分かる時は、そのprofileの通常候補・評価順を使います。隠れた相手駒・王手・ピン・千日手は推定しません。linear profileでは通常時も王、長距離移動、持駒打ち、任意成りを候補にします。どちらも相手の非公開盤面を知らないため、完全な合法性はサーバーの審判が判定します。候補を作れない場合は `422 no_observed_move` で失敗を明示します。

SQLite-backed Durable Objectを保存先に使います。D1や外部DBは使いません。局面は1手ごとのキー、requestIdレシートは対局中保持し、現在は自動削除しません。`/webhook` の `game_end` は署名済みの `{type,gameId,param,result,winner}` だけを受け付けます。終局通知とBot IDごとの重複・競合状態を同じDO transactionへ書き、commit後だけ空bodyの `204` を返します。記録にはサイト、実際のbrain版・profile hash、自己色・seat、結果、受信時刻、`param` の原文を含み、局面はそのBotの検証済み履歴を参照します。

終局記録は通常の学習データと分離したoffline-only領域です。未照合Bot、複数seat、途中参加、履歴欠落、未知brain版、終局競合は分類して `trainingEligible: false` を維持します。`/offline-review` は同じraw-body HMACとBot IDで認証するread-onlyのPOST exportで、`{type:"offline_review_export",gameId,fromPly,limit}` を受け、`param` と最大100手ずつの可視局面を返します。`param` はopaqueな文字列として保存・返却し、解析・実行・ログ出力しません。livebrainや学習candidateへ自動で混ぜる経路はありません。記録の保持期間と容量上限はまだ設定していません。

brain v5では、同じ局面で反則になった移動の成り／不成の双方が自分の観測上有効な候補なら、両方を次の試行から除外します。移動経路・行先の占有・自玉の安全性は共通です。成れない地点での成りや強制成りを省略した反則からは、有効なもう一方を除外しません。成功前の成り／不成は別候補のままなので、王手をかけるかどうかの違いは保持します。相手が着手した後へ反則による除外を持ち越しません。v4の終局記録はoffline reviewで保持し、対局途中のbrain差替えは拒否します。

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

旧state回帰の `test/fixtures/legacy-game-state-6874345.json` は、新形式からfieldを削除したものではなく、commit `68743456798c6088e66e9b343e7451037c1f55ef` の実Workerで初回・差分を処理した保存snapshotです。さらに `bc0f1ac` の実Workerがその旧stateへ拒否receiptを保存する不具合snapshotも含みます。再生成には両commitのWorker sourceを別のローカルdirectoryへ取得し、`node test/fixtures/generate-legacy-state.mjs <6874345-worker-source> <bc0f1ac-worker-source>` を使います。generatorは各版が依存する4つのsource blob SHAを照合し、旧版の再送が200・同一応答であることと、旧版から `bc0f1ac` への移行失敗を確認します。CIは保存済みfixtureを新版へ読み込んで検証し、ネットワークや本番DB移行を使いません。

本番用Cloudflare設定とtest-onlyの両Wrangler設定は `2026-09-08` を使います。GitHub Actionsは依存関係をインストールし、この日付のままCf build、`test:workerd`、生成bundle検証を実行します。`test:bundle`はCfの実生成設定から日付を読み、overrideせずにbundleを検証します。

Cloudflare CLIでWorkerをローカル起動する場合は、`.dev.vars` にローカル専用の `WEBHOOK_SECRET` を設定し、次を実行します。`BOT_ID` の既存設定は残していますが、runtimeでは照合に使用しません。値はコードへ書かず、このファイルをGitへ追加しないでください。

```sh
npm install
# .dev.vars にローカル専用の値を設定（このファイルはGit管理外）
npm run dev:cf
```

`dev:cf` は `cf dev --local` を使い、`.wrangler/cf-local` へローカル状態を保存します。`cf deploy` はこの手順に含めません。

## Cloudflare設定

- `cloudflare.config.ts` をCfのプロジェクト設定とし、`GameState` / `BetaArena` のSQLite Durable Object exportと `GAME_STATE` / `BETA_ARENA` bindingを宣言します。Cf移行時に生成した `wrangler.config.ts` では型生成を無効にしています。旧 `wrangler.toml` はテスト用設定として保持します。
- Cf beta.12の変換器は同一Workerを参照するDOにも`script_name`を付け、Dashboardの同一Worker表記とのstrict比較で競合します。`npm install`の`postinstall`と`npm run build:cf`の事前処理が、固定版`@cloudflare/config@0.23.0`のDO変換だけに互換パッチを適用します。参照先が設定のWorker名と等しい場合だけ`script_name`を省略し、外部Workerの参照とstrict判定は維持します。パッケージの版と配布ソースのSHA-256が想定と違う場合は停止します。依存更新時はこのパッチと回帰を再検証してください。npmのinstall scriptsを有効にする必要があります。 remote-config生成器のDO変換にも限定パッチを適用し、APIにない`script_name`・`environment`をown `undefined`キーとして追加しないようにします。明示された外部Worker名・environmentの値は維持します。
- version preview URLは `worker.previewUrls: false` を明示します。固定版Cfの実生成設定にもfalseが残ることを `test:bundle` で検証します。通常の `workers.dev` 公開URLを無効にする設定ではありません。互換日付は `2026-09-08` です。設定値は `test:workerd` と `test:bundle` がそれぞれruntimeとCf生成物で確認します。[Cf公式設定](https://developers.cloudflare.com/cf/projects/cloudflare-config/)
- `observability.logs` はWorkers Logsへの永続化を有効にし、`/webhook` と `/offline-review` の応答についてallowlist済みの構造化イベントを1件出力します。イベントにはHTTP status、固定error code、elapsed、Worker version ID、strategy version、検証済みの通常局面と返したCSA手を必要に応じて含めます。終局・reviewイベントにはevent種別と固定statusだけを記録し、game ID、Bot ID、本文、署名、`param`、プレイヤー名、IP、任意のraw errorは記録しません。相手のlastMoveは規定のmask表現だけを記録します。`invalid_position`の場合は固定されたvalidation stage、局面番号、失敗値の型分類だけを追加し、値自体は記録しません。無効な`lastCapture`文字列には`empty_string`、`lowercase_piece_code`、`other_string`の固定分類を加えます。新しいDB、Logpush先、Workerリソースは作成しません。
- Cloudflareの現行Workers Logs料金表ではFree枠は200,000 events/day・3日保持、Paid枠は20 million events/month込み・7日保持で、超過分は課金対象です。2026-12-01から料金体系がCloudflare Observability pricingへ移行予定です。1 webhookにつき1イベントを保存する設定のため、実際のアカウント利用量は配備後に確認してください。[料金と保持期間](https://developers.cloudflare.com/workers/observability/logs/workers-logs/)
- production設定の `BOT_ID` は既存値 `DoCiAI`、test-only runtimeの `wrangler.runtime.toml` はfixture ID `fixture-bot-id` を維持します。いずれも既存設定との互換性のため残し、runtimeは固定値との一致を要求せず、受信headerのID形式だけを検証します。Durable Objectは従来どおり `gameId` で識別し、保存済み対局履歴・request receiptへ到達できるよう名前空間を変更しません。`WEBHOOK_SECRET` は値を含まないSecret binding宣言です。秘密値をソースやログに出力しないでください。ローカル値はGit管理外の `.dev.vars` に設定します。

この構成のテストはCloudflareアカウントへ接続せず、fixturesとローカルworkerdを使います。PR時点でCloudflareリソースの作成、配備、secret設定、サイトへのBot登録は行っていません。Cloudflare上のbuild/deploy checkやruntimeリクエストの成功とは区別してください。

`test:bundle` は先に成功した `cf build` の実生成物を必須入力にします。固定版Cf CLIと同じ版のBuild Output readerでmanifestを読み、bundleから `GameState` とfetch handlerをimportし、SQLite exportと `GAME_STATE` のself-binding、値を持たないSecret宣言を検証します。同版のMiniflare/workerdへmanifestのES modules・compatibility date・DO bindingを渡し、生成bundleを変更せずraw-byte HMAC、署名なし・改竄・bodyhash不一致、古い時刻の拒否、再起動後のreceipt再送、同時要求の競合、差分履歴と反則後の指し手を検証します。テスト用のBot IDと既存fixtureのSecret値だけをローカルbindingへ渡し、外部fetchは拒否します。5分の両側の厳密境界は生成exportをNode.jsで時刻固定して検証し、7秒の受信stream timeoutとcancelも生成fetch handlerをNode.jsで直接呼び出して確認します。

SQLite rollbackと2.5秒のRPC timeout後のlate commitは、生成bundleの `GameState` を継承するメモリ上のtest-only wrapperで故障注入します。実際のSQLite書込み拒否後に局面・session・receiptが残らないこと、再送で成功すること、遅延commitのreceiptが再利用されることを検証し、SQLが利用できることも確認します。このwrapperは生成物を書き換えず、配備しません。5項目を検証する `test:workerd` は `wrangler.runtime.toml` のtest-only構成を使い、Cf生成物の検証とは別です。新しいテストではcompatibility dateのoverrideをしません。CfのBuild Output readerはbetaの内部APIなので、Cf CLIを更新する場合はこのテストも再検証してください。
