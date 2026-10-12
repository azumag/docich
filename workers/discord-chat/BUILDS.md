# Cloudflare Workers Builds

Cloudflare側のproject rootは `workers/discord-chat`、Worker名は `docich-discord-chat`。通常のwatch対象はこのディレクトリとcanonical personaです。`builds-plan.json` は設定案でありCloudflareへ自動適用されません。

Build command:

```sh
npm run build:cf
```

Deploy command:

```sh
./node_modules/.bin/cf deploy --prebuilt --worker docich-discord-chat --tag "$(git rev-parse HEAD)"
```

Production branchは `main`、preview buildは無効を前提とします。Discord Bot TokenはCloudflare secretとして別管理し、Build variableやrepositoryへ置きません。

GitHub Actionsの `Discord Chat Cloudflare` はbuildとoffline contractsだけを実行し、deploy、Discord接続、Workers AI推論は行いません。Cloudflare Builds成功も実Gateway接続成功を意味しないため、deploy後は `/healthz` とDiscord上のメンション往復を別に確認します。


## Script API更新後にstrict競合で止まった場合

`cf@1.0.0-beta.12` の `cf deploy` はstrictを内部で有効にし、`--strict` / `--no-strict` を受け付けません。直前の更新がScript API経由の場合、buildとテストが成功してもdeployだけが停止します。エラーにある「remove the --strict flag」を通常のcfコマンドへ追加して解決することはできません。

復旧は、対象Workerの配備履歴・mainのコード・binding名と型・既存Durable Object・モデル・cronを確認してから行います。不明な設定変更を無条件に上書きしません。配備時のDiscord Gateway一時切断について所有者の許可があることも確認します。

1. 最新のレビュー済みmainでCI成功を確認し、同じ `cloudflare.config.ts` を使う次のコマンドをローカルで `--dry-run` 付きで検証します。通常のcf buildと生成コードが一致することを確認します。
2. 既存のCloudflare Builds triggerの **deploy commandだけ** を、復旧用に一時変更します。build command・main限定・対象パス・資格情報の設定は維持します。

   ```sh
   ./node_modules/.bin/wrangler deploy --experimental-new-config --strict=false --tag "$(git rev-parse HEAD)"
   ```

   Wranglerは同じWorker設定とソースを読みます。runtime-test用TOMLを使わず、別Botや別Durable Objectを作りません。secretを設定ファイルへ移したり、読み戻したり、`--secrets-file` を指定したりしません。既存secretは通常のWrangler配備で保持されます。Cloudflare設定にある環境変数の値を診断出力へ出しません。
3. 確認済みmainのcommit hashを指定してビルドします。ビルド・契約テスト・配備を別々に確認し、配備されたバージョンと `/healthz` の `codeVersion`、`configured`・`connected`・`ready` を確認します。Durable Objectへのコード反映は遅れることがあるため、アップロード成功だけを完了扱いしません。
4. 成功後、deploy commandを上記の通常の `cf deploy --prebuilt` に戻します。通常コマンドでも同じmainを一度ビルド・配備して、競合が解消したことを確認します。strictを解除したコマンドを恒久設定にしません。

Buildsの復旧成功と、VCでの文字起こし・音声応答・再生の成功は別の確認です。今後のコード反映はCloudflare Builds経由で行い、通常のScript API直接更新で同じ競合を再発させないようにします。
