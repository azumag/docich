# docich Discord Chat on Cloudflare

Discord Gatewayへ常時接続する会話BotのCloudflare実運用版です。Workerの1分CronがsingletonのSQLite-backed Durable Objectを起動し、Durable ObjectがGateway WebSocket、heartbeat、session resume、メンション処理、長期記憶を担当します。LLMはWorkers AI bindingを直接利用し、外部LLM APIキーは使いません。

## Runtime

- Discord Gateway: Durable Objectのoutbound WebSocket
- 再接続: heartbeat ACK監視 + session ID/sequence/resume URLの永続化 + DO alarm + 1分Cron
- 記憶: Durable Object SQLite
- LLM: Workers AI、既定 `@cf/zai-org/glm-4.7-flash`
- Persona: `src/docich/comment/prompts/comment_persona_main.md` を `cloudflare.config.ts` がbuild時に直接読み込む。複製しない
- Secret: `DISCORD_BOT_TOKEN` のみ
- Scope: Botが参加している全Guild/Channelの明示メンション。DM、Bot、Webhook、system messageは対象外

記憶は `guild_id + channel_id + author_id` で分離し、直近6往復と入力に関連する古い4往復を使用します。`@Bot 記憶を削除` は本人の当該チャンネル記憶を本文ごと削除し、重複配信防止用IDだけを残します。

## Build

Node.js 22.18以上。

```sh
cd workers/discord-chat
npm install
npm run build:cf
```

`cf build`、オフラインNodeテスト、ローカルworkerd上のSQLite Durable Object統合テストを実行し、DiscordやWorkers AIへは接続しません。workerd統合ではproduction `DiscordBot` DOの生成と、記憶の保存・同一scope想起・別Guild分離・削除scrubを実SQLiteで確認します。

## Discord settings

Developer PortalでMessage Content IntentをONにします。必要権限は原則として View Channels / Send Messages / Read Message History / Send Messages in Threads。Guild Members、Presence、Administratorは不要です。

BotをPublicにする場合でも、招待URL自体を秘密境界にはしません。知らないGuildへの導入を後から制限したくなった場合は、Gateway接続とは別にallowlistを追加します。

## Deploy

Cloudflare CLI `cf` を優先します。コードはDiscord Tokenなしでも安全にdeployでき、その場合Gatewayには接続せず `configured:false` で待機します。

```sh
cd workers/discord-chat
cf deploy
```

その後、Bot TokenだけをCloudflare Secretとして追加します。値はリポジトリやbuild変数へ置きません。

```sh
npx wrangler secret put DISCORD_BOT_TOKEN --name docich-discord-chat
```

Secret追加後は次の1分CronでGateway接続を開始します。単独secret更新は現行Cf CLIでは未対応のため、この操作だけWranglerを使います。

TokenをGit、Issue、PR、Actions output、Workers Logsへ出しません。

## Model

既定モデルは `@cf/zai-org/glm-4.7-flash`。モデル変更は `cloudflare.config.ts` の `WORKERS_AI_MODEL` bindingだけを変更します。コードはWorkers AI native bindingのChat Completions形を使い、tool callは受理しません。

より高品質が必要なら `@cf/zai-org/glm-5.3-flash`、長い文脈や別特性が必要なら `@cf/deepseek-ai/deepseek-v4-flash-0731` 等へ切替可能です。モデルごとの課金条件はdeploy前にCloudflareの現行pricingで確認します。

## Health

公開HTTPは `GET /healthz` のsanitized状態だけを返します。会話本文、Token、Guild/Channel/User ID、LLM promptは返しません。

```json
{"configured":true,"connected":true,"ready":true,"pending":0,"fatal":null}
```

fatal close（認証失敗、disallowed intents等）は15分のcooldownを記録し、その間は再IDENTIFYしません。cooldown後にだけ再試行するため、設定修正後は自動復帰でき、恒久fatal stateにもなりません。

## Migration boundary

既存 `src/docich/discord_chat.py` / Docker版はreference implementationとローカルフォールバックとして残します。Cloudflare版の実運用確認が済むまでは既存版を削除しません。実Discord往復、実Workers AI品質、実課金、Cloudflare上のWebSocket長期維持はdeploy後の受入項目です。
