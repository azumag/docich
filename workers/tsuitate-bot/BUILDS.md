# Cloudflare Workers Builds 設定と配備状況

GitHub `azumag/docich` のmain更新をCloudflare Workers Buildsでbuild・配備します。GitHub Actionsには配備jobを追加せず、既存ActionsのWorkerテストを維持します。本人がmain連携、root `workers/tsuitate-bot` を設定済みです。この文書と `builds-plan.json` はCloudflareが自動読込する設定ファイルではありません。**対象ディレクトリのPRをmainへmergeすると既存Buildsの本番配備が発火し得るため、親レビュー・merge判断へ引き継ぎます。**

2026-10-03 12:27 UTCの本人提供Builds結果では、Cf build、Nodeテスト32件、test-only workerd4組、生成bundle検証が成功し、deployはstrict conflictでuploadを中止しました。差分はremote `compatibility_date=2026-10-03` に対するlocal `2026-09-21` と、remote `preview_urls=false` に対するlocal既定trueです。「Deploy complete」の表示だけでは成功とせず、upload abortedと非ゼロexitを失敗として扱います。本修正はlocal `previewUrls: false` を明示し、固定Cf beta.12の生成設定を検証します。互換日付は `2026-09-21` を維持し、管理画面側を同日に揃える操作は本人側の確認事項です。strictを外して強行しません。

本人提供画像では公開URL `https://docich-tsuitate-bot.tsubasa-azumagakito.workers.dev` を確認しています。URLの存在とBOTコードの配備成功、署名付き対局応答の確認は別です。`previewUrls: false` はversionごとのpreview URLの設定であり、この通常公開URLやBuildsのbranch preview設定とは異なります。[Cf公式設定](https://developers.cloudflare.com/cf/projects/cloudflare-config/)

## 起動条件の制約と有効化gate

要望はmainの `workers/tsuitate-bot/**` 更新だけでbuildし、それ以外ではbuild自体を起動しないことです。通常のpushではCloudflareのwatch pathsでこの範囲に絞れます。Cloudflareのワイルドカードは `*` が任意の文字列（階層を含む）に一致する仕様なので、GitHubのglobをそのまま転記せず、includeを `workers/tsuitate-bot/*` の1件にします。パスはリポジトリ基準で、root directoryを指定しても `src/*` に縮めません。excludeは空です。workflow、共通依存、他のWorker、repo rootの文書だけの更新をincludeへ足しません。[watch paths公式仕様](https://developers.cloudflare.com/workers/ci-cd/builds/build-watch-paths/)

ただしCloudflareは **変更0件、変更3000ファイル以上、または20コミット以上のpush** でpath判定を迂回してbuildします。厳密な「対象外では例外なく起動しない」は標準のGit連携だけでは保証できません。build command内の変更判定では、すでに起動したbuildを止めるだけです。本人による接続設定後もこの制約は残ります。起動範囲を広げる変更や独自のhookは追加していません。

## 所有者が確認する設定値

| 項目 | 設定案 |
| --- | --- |
| Git repository | `azumag/docich` |
| Worker name | `docich-tsuitate-bot`（既存Cf設定と一致） |
| Production branch | `main` |
| Preview builds | 無効（PR・他branchから配備しない） |
| Root directory | `workers/tsuitate-bot` |
| Include paths | `workers/tsuitate-bot/*` のみ |
| Exclude paths | 空 |
| Build command | `npm run build:cf` |
| Deploy command | `./node_modules/.bin/cf deploy --prebuilt --worker docich-tsuitate-bot --tag "$(git rev-parse HEAD)"` |
| Build variables | `NODE_VERSION=24.18.0`, `CF_SEND_TELEMETRY=false` |

production branchとpreview無効化はCloudflareのBranch controlで設定します。root directoryでbuild/deploy commandを実行し、依存関係はそのpackage.jsonから標準の自動インストールを使います。lockfileは現時点でなく、Wranglerは `^4.136.0` のため推移的依存まで固定されません。`SKIP_DEPENDENCY_INSTALL` は設定せず、重複のnpm installをbuild commandへ追加しません。[branch設定](https://developers.cloudflare.com/workers/ci-cd/builds/build-branches/)、[monorepo設定](https://developers.cloudflare.com/workers/ci-cd/builds/advanced-setups/)、[build image](https://developers.cloudflare.com/workers/ci-cd/builds/build-image/)

`build:cf` はpackage内の固定版Cf CLIでbuildし、Node fixtures、test-only workerd、実生成bundleのworkerd検証を順に行います。どの段階でも失敗したら非ゼロで終了し、Cloudflareのdeploy段階へ進ませません。deploy commandは同じ生成物を `--prebuilt` で使い、生成物にmodeを指定していないためdeployにもmodeを加えません。Secretファイルのupload引数、Wranglerへの代替配備、自動再試行は含めません。[Cf build/prebuilt仕様](https://developers.cloudflare.com/cf/projects/)

CfはCIの `CLOUDFLARE_API_TOKEN` と `CLOUDFLARE_ACCOUNT_ID` を使えます。本人の実BuildsではCfのbuild/検証からdeployのstrict比較まで実行できましたが、upload成功とsource tag/Active Deploymentの一致は未確認です。現在の接続を新設し直さず、設定衝突解消後にそれらを受入確認します。[Cf CI仕様](https://developers.cloudflare.com/cf/ci/)

## 正規接続と認証の確認手順（接続初回の参考、この修正では実施しない）

以下は接続準備時の手順です。本人が設定済みの現在の接続を再作成したり、新tokenを追加したりする指示ではありません。`BOT_ID` はplaceholderのまま、実IDは本人回答待ちです。現構成のtext bindingはソース値を配備するため、管理画面へ実IDを先に設定すると次の配備で衝突またはplaceholder上書きの可能性があります。固定版Cfのtext bindingはvalue必須で、管理画面の既存text値だけを保持する設定は未検証です。ID確定後に明示値をソースへ反映する方式を基本とし、管理画面値を保持する方式が必要なら別途仕様と生成物を検証して選びます。推測値で進めません。Secretを取得・表示・設定・送信せず、HMAC必須を維持します。

1. 上記path例外と未検証事項について本人の判断を得ます。以前のMCP書込拒否はHTTP statusとCloudflare数値エラーコードが返らず、拒否元を特定できていません。別の認証を作ることで迂回しません。所有者が承認済みCloudflareアカウントと正規のWorkers Builds配備権限を確認します。
2. Cloudflare dashboardで対象WorkerとBuilds接続の現状を確認します。2026-10-03の読み取りでは16 Workers中に対象名はありませんでした。新規WorkerのImportでは **Save and Deployが即配備を開始する** ため、設定の閲覧と実行を混同しません。Worker作成・Git連携・初回配備は別途所有者の承認段階です。[接続手順](https://developers.cloudflare.com/workers/ci-cd/builds/)
3. Cloudflareの既存GitHub integrationでdocichへアクセス可能か確認し、可能ならその接続を使います。新規GitHub App/OAuthの許可が必要ならそこで止めて本人に確認します。アクセス範囲はdocichに限定します。[Git連携](https://developers.cloudflare.com/workers/ci-cd/builds/git-integration/)
4. BuildsのAPI token選択で既存の承認済みtokenを確認します。**Create new tokenは自動でtokenを生成し、KV/R2/全zone Routes等も含む既定権限が付きます。選択しません。** Buildsは現仕様でuser tokenを使用します。対象アカウント・Workerのupload/配備とSQLite GameState export/self-bindingに必要な範囲を確認します。Workers側のEditor（legacy Workers Scripts Edit相当）を基準とし、追加リソース作成権限は初回DO作成の必要性を照合して別途承認します。KV/R2/D1/Routesや課金・アカウント全体の管理権限をこのWorkerのために追加しません。[Builds認証設定](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/)、[Workers権限](https://developers.cloudflare.com/workers/authorization/workers/)
5. Buildsが注入する認証変数 `CLOUDFLARE_API_TOKEN` と `CLOUDFLARE_ACCOUNT_ID` の利用可否・承認済みaccount一致を秘密値を表示せず確認します。GitHub Secretsはこの経路の前提にしません。GitHub接続ツールはSecrets API非対応のため、既存GitHub資格情報の有無も未確認です。ローカルCfは指定版ですが未ログイン、ローカルWranglerは4.119.0で要件未満でした。Buildsがそれらのローカル認証を引き継ぐとは扱いません。
6. 表のwatch paths/main/preview無効化とcommandを保存する前に、初回buildや本番実行が発生する操作への承認を得ます。安全な接続前設定がUIでできない場合は、既定の全パスbuildで先に接続せず停止します。runtime `BOT_ID` はplaceholder、`WEBHOOK_SECRET` は値なしのbinding宣言のままです。対局用Secret設定、サイト登録、実対局は別段階です。runtime SecretをBuild variablesへ追加しません。

## 有効化後の受入確認と失敗時

通常のmain pushで対象ファイル変更はbuildし、対象外変更だけならskipとなることをBuilds履歴で照合します。README/fixture/package.jsonも対象ディレクトリ内なのでbuild対象です。他branchとPRではpreview buildも起動しないことを確認します。0/3000/20の例外は保証対象外として扱い、ローカルpathテストをCloudflareの実検証の代わりにしません。

有料planの同時build枠はアカウント単位で6、timeoutは20分です。この設定案にはWorkerごとの排他lockや古いcommitの配備抑止はありません。短時間に連続pushした際のBuildsのqueue/cancel/配備順序は未検証なので、最新mainがActive Deploymentであることの実測を有効化後の条件にします。旧commitが後から配備されない保証はしません。[Builds limits](https://developers.cloudflare.com/workers/ci-cd/builds/limits-and-pricing/)

build/test失敗は配備へ進まず、deploy失敗は成功扱いにせずbuild/version/Active Deploymentを確認します。deploy途中にリソースが作られた可能性も区別し、自動の再配備・削除・rollbackでSQLiteデータを変更しません。自動配備を止める場合は所有者が対象WorkerのBuilds接続を停止/Disconnectする正規操作を行います。既存VM配信や他Workerを操作しません。

この修正ではCloudflareへの手動deploy・設定変更、接続再作成、認証/token/Secrets追加を行っていません。作業バナー・音声もVMアクセスを広げず未実施です。
