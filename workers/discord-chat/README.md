# docich Discord Chat on Cloudflare

Discord Gatewayへ常時接続する会話BotのCloudflare実運用版です。Workerの1分CronがsingletonのSQLite-backed Durable Objectを起動し、Durable ObjectがGateway WebSocket、heartbeat、session resume、メンション処理、長期記憶を担当します。LLMはWorkers AI bindingを直接利用し、外部LLM APIキーは使いません。

## Runtime

- Discord Gateway: Durable Objectのoutbound WebSocket
- 再接続: heartbeat ACK監視 + session ID/sequence/resume URLの永続化 + DO alarm + 1分Cron
- 記憶: Durable Object SQLite
- LLM: Workers AI、既定 `@cf/deepseek-ai/deepseek-v4-flash-0731`
- Persona: `src/docich/comment/prompts/comment_persona_main.md` を `cloudflare.config.ts` がbuild時に直接読み込む。複製しない
- Secret: `DISCORD_BOT_TOKEN`。音声会話bridgeを有効化する場合のみ、別の `DISCORD_VOICE_INTERNAL_TOKEN` も使用
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

既定モデルは `@cf/deepseek-ai/deepseek-v4-flash-0731`。モデル変更は `cloudflare.config.ts` の `WORKERS_AI_MODEL` bindingだけを変更します。コードはWorkers AI native bindingのChat Completions形を使い、tool callは受理しません。

Cloudflare上の実プロンプト受入ではGLM-4.7-Flashが500 completion tokensをreasoningだけで使い切り本文を返さないケースを確認したため、会話Botの既定をDeepSeek V4 Flashへ変更しています。GLM-5.3-Flash等へ切替える場合も、canonical personaを含む実プロンプトで本文がtoken上限内に返ることを受入確認します。モデルごとの課金条件はdeploy前にCloudflareの現行pricingで確認します。

## Voice conversation bridge (Slice 3)

Windows上のDiscord Voice runtimeから、STT済み本文だけをこのWorkerへ渡して既存のpersona / Workers AI / SQLite記憶を再利用するため、`POST /voice/reply` を追加しています。通常のDiscord Bot Tokenとは分離した `DISCORD_VOICE_INTERNAL_TOKEN` のBearer認証が必須で、secret未設定時はendpoint自体を404として扱います。

Cloudflare側では十分長いランダム値をsecretとして登録します。

```sh
npx wrangler secret put DISCORD_VOICE_INTERNAL_TOKEN --name docich-discord-chat
```

Windows runtime側には同じ値を `DOCICH_DISCORD_VOICE_CHAT_TOKEN` として、WorkerのHTTPS endpointを `DOCICH_DISCORD_VOICE_CHAT_URL=https://<worker-host>/voice/reply` として設定します。Token、transcript、reply、Guild/Channel/User IDは通常ログへ出しません。

`POST /voice/reply` は既存テキスト会話の `guild + channel + user` 記憶を**読み取り**、生成中に参照元が削除された場合は返答を破棄します。この生成requestだけでは音声ターンを永続化しません。

Slice 4では、Windows runtimeがVOICEVOX合成とDiscord再生に成功した後だけ、同じBearer secretで `POST /voice/commit` を呼びます。commit時に初めて `voice:<turnId>` の会話を `sent` として保存し、reply IDは `voice-playback:<turnId>` として固定します。同一turn/contentのACK再送は `already_committed` として成功し、同じturnIdでtranscript/reply/scopeが変わった場合は409で拒否します。TTS失敗、再生失敗、barge-in、取消ではcommit request自体を送らないため、聞こえなかった返答は記憶されません。

## Health

公開HTTPは `GET /healthz` のsanitized状態だけを返します。会話本文、Token、Guild/Channel/User ID、LLM promptは返しません。

```json
{"configured":true,"connected":true,"ready":true,"pending":0,"fatal":null}
```

fatal close（認証失敗、disallowed intents等）は15分のcooldownを記録し、その間は再IDENTIFYしません。cooldown後にだけ再試行するため、設定修正後は自動復帰でき、恒久fatal stateにもなりません。

## Migration boundary

既存 `src/docich/discord_chat.py` / Docker版はreference implementationとローカルフォールバックとして残します。Cloudflare版の実運用確認が済むまでは既存版を削除しません。実Discord往復、実Workers AI品質、実課金、Cloudflare上のWebSocket長期維持はdeploy後の受入項目です。


## 内部応答生成の再利用境界

`src/conversation.js` の `generateConversationReply(env, sql, event, seq, setStage?)` は、既に登録された会話のscope記憶contextを取得し、既存 `llm.js` のpersona/Workers AI処理を呼び、`{reply, context}` を返す内部関数です。`setStage` は信頼側の診断stage更新だけを行い、未指定なら何もしません。人格、モデル、prompt、retry、tool拒否、文字側の最大901字契約を変更しません。

呼出側がadmission/認証・dedupと`beginConversation`を担当します。生成後は`validContext`で現在入力/想起元の削除を再確認し、実送信前に`markSending`、成功して返信IDを得てから`finishConversation`、失敗時に`failConversation`を実行する既存契約を維持してください。生成関数だけでは送信・保存完了になりません。文字側の忘却command、失敗通知、queue、送信ackと診断は引き続き`bot.js`が所有します。

音声live runtimeは選択TTSの既定200字制限、LLM/TTS/playback/commitの個別deadline、barge-in取消、scope、再生成功後だけの保存方針を明示的に適用します。この生成関数自体は暗黙に短縮/切断/期限変更/保存を行いません。fake音声callerテストは引き続きoffline境界を検証し、実VC・実VOICEVOX・長時間品質の受入を示すものではありません。
