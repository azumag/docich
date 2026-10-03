# Discord会話Bot：長期記憶・メンション・既存ペルソナ

## 動作

LLMを文章生成APIとして呼び、Discordのメンションに返答する。自発発言、チャンネルの全投稿への応答、MCP、CLIエージェント、ゲーム操作、配信への転載は行わない。既定停止の開発用実装であり、本番VMに自動配備・自動起動しない。

ペルソナの正本は `src/docich/comment/prompts/comment_persona_main.md`。これを起動時に読み、systemメッセージの先頭へそのまま入れる。別の人格や一人称を設定・複製しない。Discord接続ではTwitch向けの返答をDiscord向けに適用し、実際には渡されていない配信映像・ゲームの現況や操作結果を捏造しないという接続条件だけを補う。正本が欠落・空・不正なら起動しない。正本の変更反映にはBotプロセスの通常の再起動が必要。

`message.mentions` にBot自身が含まれる投稿だけがトリガー。メンションのないBot宛て返信・通常の雑談には応答せず、保存もしない。Botへの通知を伴う返信がDiscordのmentionsに含まれる場合は対象となる。Bot/Webhook/システム投稿、DM、許可外のサーバー・チャンネル、到着時点で120秒を超えた古いイベントは除外する。スレッドはそのIDを個別許可し、親チャンネルから許可を継承しない。

## 長期記憶

`DOCICH_DISCORD_MEMORY_DIR/conversations.sqlite3` にSQLiteで永続化する。起動し直しても同じ保存先を使えば過去の会話を参照できる。15分のTTLや24発言での削除は廃止し、所有者が削除しない限り記憶を保持する。既存プロトタイプのRAM履歴や、導入前のDiscord履歴を遡って取り込む機能はない。

記憶の範囲は **同一サーバー・同一チャンネル・同一DiscordユーザーID**。表示名が変わっても同じ人物として扱い、他人・別チャンネル・別サーバーの履歴を混ぜない。多人会話全体を共有記憶にするものではない。

応答時は、送信済みの直近6往復と、今回の発言に関連する古い会話を最大4往復取り出す。日本語の隣接2文字・正規化した英数字の語を索引にし、直近の枠から外れた古い話題も検索する。検索は記録された本人の発言を根拠とし、モデルが作った個人プロフィールを事実として蓄積しない。埋め込みAPIや追加の要約LLMは使わない。類義語や暗黙の話題への完全な意味検索、全履歴の常時投入、完璧な想起を保証するものではない。

保存するのは受理したメンション本文・話者ID/表示名・メッセージID・時刻・返信先と、正常に送信できたBotの返答。LLMや送信が失敗した会話は想起せず、失敗処理で本文を消す。クラッシュ中の未完了レコードは自動再実行・想起しない。入力は1件2000文字、名前は80文字まで、返答は最大901文字に制限する。

## 返答待ち・失敗・削除

LLM生成は全体で1件ずつ、処理中を含めて最大32件のメンションを順番待ちにする。元の実装のように生成中のメンションを黙って捨てない。上限時は固定の混雑メッセージを返す。LLM/保存失敗では固定の失敗通知を返し、有料モデルへの切替や自動再試行はしない。Discord送信結果が不明な失敗では二重投稿を避けるため再送しない。正常な再起動をまたいだ既知メッセージの重複は永続IDで除外するが、外部APIとのexactly-once配信は保証しない。待機中にプロセスを失った投稿の自動再処理も対象外。

本人が `@Bot 記憶を削除` と投稿すると、そのチャンネルにおける本人とBotの会話本文・名前・検索索引を消す。これはLLMへのお願いではなく、本人のユーザーIDで範囲を固定したアプリ側処理。他人や別チャンネルの記憶は消さない。

受信できた元投稿の削除・本文編集、Bot返信の削除でも対応する会話ペアを想起対象から取り除く。生成中に元投稿や参照した記憶が削除された場合は、その内容に依存する返答を送らない。編集後の本文は自動再生成せず、新しいメンションを必要とする。

再配信による復活防止のため、削除後も本文のないメッセージID・サーバー/チャンネル/ユーザーID等の処理済みメタデータは保持する。SQLiteはsecure_deleteとDELETE journalを使うが、バックアップ、LLM提供者側の記録、Discord上の既存投稿、他の会話に引用された内容まで遡って完全消去するものではない。切断中に取り逃した削除イベントも検出しない。完全な会話削除には本人の記憶削除操作を使い、必要なら所有者が保存先とバックアップを管理する。

## 保存先と権限

実行環境はPython 3.11以上・POSIX（Linux/macOS）。SQLite 3.35以上を使用する。保存先はリポジトリ外の明示した絶対パスとし、所有者のみが読書きできるディレクトリ（0700）/ファイル（0600）を用意する。保存先の親ディレクトリは先に作成しておく。共有権限・他人所有・DBのsymlink・未知のスキーマや破損したDBは失敗とし、空のRAM記憶へ黙ってフォールバックしない。

同じ保存先を2プロセスから同時に開くことはロックで禁止する。コンテナではこのディレクトリを永続ボリュームに置く。ファイルは平文なので暗号化は実行環境側で管理し、Git・Actions artifact・通常ログへアップロードしない。保持に時間上限を設けないため容量は増える。所有者がディスク容量とバックアップの扱いを管理する。本番のstorage diagnosticsへの登録は未実装。

## APIと設定

LLMはChat Completions互換の `POST <LLM_BASE_URL>/chat/completions`。`model`、`messages`（system/user/assistant）、`stream=false`、`max_tokens=500`を送り、`choices[0].message.content`の文字列だけを使う。ツール呼び出しを受理・実行しない。HTTPリダイレクトと環境由来プロキシを禁止し、応答64KiB/ソケットタイムアウト45秒を上限とする。45秒は厳密な総実行時間上限ではない。モデル・API実装ごとの互換性は実接続で確認する。

| 環境変数 | 内容 |
|---|---|
| `DOCICH_DISCORD_GUILD_ID` | 対象サーバーID（必須） |
| `DOCICH_DISCORD_CHANNEL_IDS` | チャンネルIDのカンマ区切り（必須、最大8件） |
| `DOCICH_DISCORD_TOKEN` | Bot Token（必須、秘密） |
| `DOCICH_DISCORD_LLM_BASE_URL` | APIベースURL（必須、例 `http://127.0.0.1:8080/v1`） |
| `DOCICH_DISCORD_LLM_MODEL` | APIで使用するモデルID（必須） |
| `DOCICH_DISCORD_LLM_API_KEY` | 必要な提供先のみ設定（秘密） |
| `DOCICH_DISCORD_MEMORY_DIR` | リポジトリ外の永続保存ディレクトリ（必須、絶対パス） |
| `DOCICH_DISCORD_ALLOW_HTTP` | 非ループバックHTTPを使う信頼した私設網等でだけ`1`。通常はHTTPS |
| `DOCICH_DISCORD_ENABLED` | Discordに接続するときだけ`1` |
| `DOCICH_ALLOW_REAL_AI` | LLM利用の明示許可`1`。上の有効化と両方必要 |

旧 `DOCICH_DISCORD_MODE=channel` と `DOCICH_DISCORD_PERSONA` は起動エラーにする。設定を削除し、メンションのみ・既存ペルソナに移行する。互換用の `MODE=mentions` だけは受け付ける。

Bot設定でMessage Content Intentを有効化し、対象チャンネルの閲覧・投稿・必要な履歴閲覧権限だけを与える。スレッドではスレッド投稿権限も必要。管理者・メンバー一覧・Presence・音声の権限は不要。

参加者にはAI Botであること、メンション会話の永続保存、設定したLLM提供先へ過去の関連会話も送信されること、削除方法を知らせる。提供先の保持設定も確認する。Bot Token/APIキーをチャット・Git・Issue・Actions出力へ貼らない。SDKのDEBUGログも有効にしない。

## 開発環境での起動

所有者の安全な方法で環境設定を置いた後、まず設定とペルソナだけ確認する。`--check`はDBを作らずネットワークにも接続しない。保存先の実権限・API認証・モデル対応の確認までは行わない。

```sh
PYTHONPATH=src python3 -m docich.discord_chat --check
```

許可した開発環境のみにSDKを導入する。本番VMでこの手順を直接実行しない。

```sh
python3 -m pip install -r requirements-discord.txt
DOCICH_DISCORD_ENABLED=1 DOCICH_ALLOW_REAL_AI=1 \
  PYTHONPATH=src python3 -m docich.discord_chat
```

## 検証と本番との区別

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_discord_chat.py tests/test_discord_memory.py
```

テストは実際の一時SQLiteファイルの再オープン、古い日本語話題の想起、記憶の分離/削除、重複、キュー/キャンセル、正本ペルソナ、API/Discord配線をオフラインで確認する。SDK導入済みCIでは実discord.Client構築も行う。実Discord/LLMとの往復・口調の品質・遅延・利用料金・実権限は別の受入確認が必要。

既存の配信・ゲーム・Soren gitlink・音声・モデル設定・本番workflowは変更しない。共通LLM dispatcherのCLI経路やフォールバックも使用しない。本番常駐化に必要なruntime registry/manifest、worker health、queue registry、structured telemetry、diagnostics、予算上限、owner停止操作、canonical deploymentは別の統合段階に残る。現段階を本番稼働済み・production readyとは扱わない。

参考: Discord Gateway/Intents https://docs.discord.com/developers/events/gateway 、discord.py https://discordpy.readthedocs.io/en/stable/ 。

## Dockerでの独立運用

Docker EngineとCompose v2（Dockerfile固有のignoreとmulti-stage build対応）が必要。ホストへpip/venvを導入せず、次のオフライン検証を実行する。実Discord/外部LLMへ接続せず、模擬secret・ループバックHTTP・専用一時volumeだけを使う。Docker daemonや既存サービスの設定は変更しない。

```sh
python3 containers/discord-chat/verify.py
```

公式Python `3.12.15-slim-bookworm` をDockerfile記載のmanifest digestへ固定する。runtimeにはDiscord専用依存・Bot/記憶ソース・正本ペルソナ・volume保守スクリプトだけをCOPYし、pytestと模擬データは別test stageに置く。Dockerfile固有のexact-path allowlistでGit、handoff、env、DB、ログ、他サービスのコードをcontextから除外する。検証ではLinuxのSQLite 3.35以上、flock、discord.py、正本ペルソナのSHA-256一致、runtimeのテスト依存/データ不在も確認する。

### 設定・ビルド・確認

`containers/discord-chat/settings.env.example` をリポジトリ外の所有者専用設定ファイルへコピーする。Bot、サーバー、1〜8チャンネル、APIベースURL、モデルは所有者の指定値を設定する。APIキーが不要ならkey overlayを使わない。secret値は設定ファイルへ書かず、所有者の安全な手段で外部ファイルへ保存する。

Linuxではsecretファイルを実行UID/GID `65532:65532` が読める所有権・`0400`（親ディレクトリもアクセス可能）で準備する。Composeのfile secretはbind mountであり、Composeのuid/mode指定で変換されるとは仮定しない。Docker Desktopの共有ファイル権限も実環境で下の`--check`により確認する。読めなければ所有者が専用ファイルの所有権を調整し、root実行やworld-readable化はしない。シンボリックリンク、非regular file、worldアクセス、不正ASCII/空/4096バイト超を拒否し、エラーにpathや値を表示しない。既存のTOKEN/API_KEY環境変数方式は維持し、非空の値と_FILEの併用はエラーとする。

```sh
# 実際のリポジトリ外の設定pathへ置き換える。
SETTINGS=/absolute/private/discord-settings.env
cp containers/discord-chat/settings.env.example "$SETTINGS"
chmod 600 "$SETTINGS"
# 所有者が設定を編集し、外部secretファイルを準備してから続ける。
# APIキーが必要な場合だけ配列にoverlayを追加する。
DC=(docker compose --env-file "$SETTINGS" -p docich-discord -f compose.discord-chat.yml)
# DC+=(-f compose.discord-chat-api-key.yml)
DC+=(--profile discord-chat)
"${DC[@]}" build discord-chat
# ネットワーク/DB書込みなしの設定・ペルソナ・secret読取確認。
"${DC[@]}" run --rm -T --no-deps discord-chat --check
```

上の配列例はbash/zsh用。`--check`はDiscord接続・API認証・保存先権限を証明しない。設定には`/chat/completions`を含めない。コンテナの`127.0.0.1`はホストを指さない。外部HTTPS、別コンテナ、ホスト、Tailscaleのどの経路を使うか所有者が明示し、コンテナから届くURLを設定する。私設HTTPを許可する場合だけ`DOCICH_DISCORD_ALLOW_HTTP=1`。ホストAPIのbind範囲、daemon、firewallをこの手順で変更しない。

### 明示起動・停止・更新

非本番環境の接続先と利用範囲を承認した後だけ、設定ファイルの`DOCICH_DISCORD_ENABLED`と`DOCICH_ALLOW_REAL_AI`を両方`1`にする。既定は両方`0`、profile指定が必要、restart policyは`no`。既存配信Composeとは独立している。

```sh
"${DC[@]}" up -d --no-build discord-chat
"${DC[@]}" ps discord-chat
# 固定イベント/エラーだけを確認し、SDK DEBUGやenv/config/inspect全文を出さない。
"${DC[@]}" logs --tail 30 discord-chat
"${DC[@]}" stop discord-chat
"${DC[@]}" start discord-chat
# 更新前に停止・バックアップし、旧イメージを保持する。
docker image tag docich-discord-chat:local docich-discord-chat:rollback
"${DC[@]}" stop discord-chat
"${DC[@]}" build discord-chat
"${DC[@]}" up -d --no-build --force-recreate discord-chat
# ロールバック（同じ記憶volumeを維持）。
"${DC[@]}" stop discord-chat
docker image tag docich-discord-chat:rollback docich-discord-chat:local
"${DC[@]}" up -d --no-build --force-recreate discord-chat
# コンテナ/networkの撤去でもvolumeは保持する。
"${DC[@]}" down
```

タグ操作例はサンプルの`DOCICH_DISCORD_IMAGE=docich-discord-chat:local`を使う場合。別タグを設定した場合は同じタグへ読み替える。ペルソナ更新も再ビルド・再作成で反映する。通常の停止・更新・撤去で`down -v`やvolume pruneは実行しない。

SIGTERM/SIGINTで新規受付を停止し、待機中会話をcancelし、実行中同期HTTPスレッドをjoinしてからSQLiteとDiscordをcloseする。送信は10秒、Compose停止猶予は90秒。HTTPの45秒はソケットタイムアウトであり総時間上限ではなく、低速な応答等で90秒を超える場合の強制停止まで安全終了は保証しない。クラッシュ後のpending/sendingは再起動時に本文を消してfailed化し、メッセージIDは二重送信防止用に保持する。外部APIとのexactly-onceは保証しない。

### 記憶のバックアップ・復元

固定project名`docich-discord`の専用named volumeは`docich-discord_memory`。初回にイメージ内の専用ディレクトリの所有権が空volumeへ継承され、ディレクトリ0700、DB/lock/journal等0600、UID/GID 65532で使用する。不明な既存データの再帰chownや初期化はしない。

Botを停止し、リポジトリ外のバックアップを作る。SQLite backup APIと専用flockを使い、DB本文を画面やartifactへ出さない。backupのstdoutだけがbinary DBで、restoreは新規の空volumeだけ受け付ける。

```sh
"${DC[@]}" stop discord-chat
umask 077
BACKUP=/absolute/private/discord-memory.sqlite3
MAINT=(docker run --rm -i --network none --read-only --cap-drop ALL
  --security-opt no-new-privileges:true --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m)
"${MAINT[@]}" --mount type=volume,source=docich-discord_memory,target=/var/lib/docich-discord \
  --entrypoint python docich-discord-chat:local /opt/docich/volume.py backup > "$BACKUP"
# 必ず新しい名前を使い、既存volumeへ復元しない。
docker volume create docich-discord_memory-restored
"${MAINT[@]}" --mount type=volume,source=docich-discord_memory-restored,target=/var/lib/docich-discord \
  --entrypoint python docich-discord-chat:local /opt/docich/volume.py restore < "$BACKUP"
```

保守コマンドの終了コードを確認し、成功したbackupだけを保存する。復元を採用する際は所有者が外部Compose overrideで`volumes.memory.name: docich-discord_memory-restored`を指定し、`DC`へそのファイルを追加して再作成する。元volumeは検証・ロールバックまで保持する。バックアップも平文なので所有者がアクセスと保持期間を管理する。

`@Bot 記憶を削除`は本人・当該チャンネルの本文/名前/索引だけを消し、処理済みIDを保持する。volume全削除は全参加者・全チャンネルの記憶とIDを失う別操作であり、通常手順には含めない。既存バックアップとLLM提供者の記録はBotの削除操作で消えない。

### 隔離の範囲と受入確認

非root固定UID/GID、read-only rootfs、全capability削除、no-new-privileges、Docker標準seccomp、init、16MiBのnoexec/nosuid/nodev tmpfsを使用する。記憶volume全体とtmpfsだけがアプリの書込み先で、secretはread-only。上限はメモリ/swap合計256MiB、CPU 0.5、PID 64、ログ1MiB×3。host network/PID/IPC、privileged、device、Docker/SSH socket、ホストhome/既存運用ディレクトリは使わず、ポートも公開しない。

専用bridgeは他サービスから分離するが、Discordと指定APIへの外向き通信を可能にするため、LAN/インターネットへのegress allowlistは適用していない。Dockerは完全なVM隔離ではない。ホスト側egress制御、Rootless Dockerの新規導入、productionのruntime/health/diagnostics/control-plane統合は別作業。

オフライン検証は空volume・再作成後の想起・スコープ分離/削除・二重起動拒否・適用隔離・HTTP処理中SIGTERMと待機キュー・整合backup/restoreまで確認する。通常CIで外部APIを呼ばない。実Discord/LLM往復、実権限、品質、料金、実運用での停止/復元は未確認であり、所有者指定の非本番ホスト、Bot Token、サーバー/チャンネル、API URL/必要ならキー、モデル、利用範囲が必要。
