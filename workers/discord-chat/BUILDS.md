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
