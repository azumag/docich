# Discordで会話するLLM Bot（初期プロトタイプ）

目的は、Discordを外部AIから読み取るMCPではなく、Discord上で会話に参加するBotをdocichに作ること。
現段階はテキスト会話の開発用プロトタイプ。既定で停止し、本番配備・自動起動・配信との連動は行わない。

## 初期機能と境界

- `mentions`（既定）：直接メンションとBot宛て返信に応答する。`channel`：明示したBot専用チャンネルの通常発言にも応答する。
- 話者ID・表示名・返信先IDと直近の文脈を渡し、短く自然な日本語で応じる。口調は所有者の環境設定で変更可能。人間になりすまさない。
- 設定した1サーバー、最大8個のチャンネルIDだけが対象。スレッドも個別IDの明示が必要で、親チャンネルから自動許可しない。DM、Bot、Webhook、システム投稿、空本文、120秒を超える古いイベントには反応しない。
- 会話履歴は各チャンネル最大24発言・文脈参照期間15分。RAM内のみで再起動すると消える。TTLの削除はイベント処理時の遅延掃除であり、無活動中のメモリを15分ちょうどに消去するタイマーではない。Discord SDKの全体メッセージキャッシュは無効。
- 履歴の過去分を取得するREST巡回、画像/添付取得、長期の個人プロフィール作成、学習はしない。受信できた削除・本文編集イベントで対象発言を文脈から除外し、生成中の対象が消えた場合は送信しない。切断中の未受信削除や、すでに生成済みの返信に含まれた内容まで完全消去するものではない。
- 全体でLLM生成は1件だけ。完了後のクールダウンは`mentions`で2秒、`channel`で10秒。混雑中の発言は文脈には残すが後追いの応答キューには入れない。重複防止はプロセス内の直近256イベントに限定し、永続的なexactly-once配信は主張しない。
- 返答は最大901文字で1投稿。すべてのメンション通知とリンク埋め込みを無効にし、エラー本文・プロンプト・生成本文・資格情報はこのモジュールのログに出さない。SDKのDEBUGログも有効にしないこと。

## LLM接続を配信用チェーンから分離する理由

`src/docich/ai_generate.py::run_prompt()`は既存の共通入口だが、`llm/providers.py`の一部プロバイダーはCodex/OpenCodeのCLIエージェントを起動する。Discord参加者の入力でツール・ファイルアクセスを許さないため、初期版ではそこを自動流用しない。

`ChatBackend`は所有者が明示したChat Completions互換APIへ、system/user/assistantのテキストだけを送る。ツールを登録・実行せず、CLIを起動せず、モデルや有料フォールバックを自動選択しない。HTTPリダイレクトと環境由来プロキシは無効。設定済みの配信・ゲームのLLM、グローバル設定、キュー、音声には変更を加えない。

APIは`POST <LLM_BASE_URL>/chat/completions`、`messages`、`stream=false`、`max_tokens=500`を受け付け、`choices[0].message.content`に文字列を返す必要がある。互換APIすべて・すべてのモデルでの動作を保証するものではない。APIキーが不要なローカル環境ではキーを省略できる。応答は64KiBまで、ソケットタイムアウトは45秒（厳密な総実行時間上限ではない）。ツール呼び出しを含む応答や異常なJSONは送信しない。直接HTTP呼び出しは既存dispatcherの課金集計・backoff・telemetryを使わないため、実運用統合は別途必要。

## 開発用セットアップ

Python 3.11以上の分離された開発環境を使用する。本番VMでこの手順を直接実行しない。

1. Discord Developer PortalでBotアプリを作成し、Botであることが分かる名前・プロフィールにする。
2. `Message Content Intent`を有効化する。サーバーへ追加する際は対象チャンネルの閲覧・送信・履歴閲覧だけを許可する。スレッドで使う場合はスレッドへの送信も必要。Administrator、サーバー管理、メンバー一覧、Presenceは不要。
3. 対象の参加者にAI Botであることと、許可チャンネルの直近会話が設定したLLM提供先へ送信されることを知らせる。提供先のデータ保持設定を確認する。
4. 次の設定を所有者の安全な環境設定へ保存する。Bot Token/APIキーをチャット、Git、Issue、Actions出力へ貼らない。

| 環境変数 | 設定内容 |
|---|---|
| `DOCICH_DISCORD_GUILD_ID` | 対象サーバーID（必須） |
| `DOCICH_DISCORD_CHANNEL_IDS` | 対象チャンネルIDのカンマ区切り（必須、最大8件） |
| `DOCICH_DISCORD_TOKEN` | Bot Token（必須、秘密） |
| `DOCICH_DISCORD_LLM_BASE_URL` | 互換APIのベースURL（必須。例：`http://127.0.0.1:8080/v1`） |
| `DOCICH_DISCORD_LLM_MODEL` | そのAPIで使えるモデルID（必須） |
| `DOCICH_DISCORD_LLM_API_KEY` | LLM用APIキー（必要な提供先のみ、秘密） |
| `DOCICH_DISCORD_MODE` | `mentions`（既定）または`channel` |
| `DOCICH_DISCORD_PERSONA` | 任意の口調設定。既定は気さくで落ち着いた会話仲間 |
| `DOCICH_DISCORD_ALLOW_HTTP` | 非ループバックへのHTTPを使う場合だけ`1`。信頼できる私設網等に限定し、通常はHTTPSを使う |
| `DOCICH_DISCORD_ENABLED` | 実際にDiscordへ接続するときだけ`1` |
| `DOCICH_ALLOW_REAL_AI` | 実際にLLMを呼ぶときだけ`1`。Discord側の有効化と両方必要 |

設定確認はSDKのインストールやネットワーク接続を伴わない。

```sh
PYTHONPATH=src python3 -m docich.discord_chat --check
```

所有者が対象の開発Botへの接続と選択モデルの利用を許可したあと、分離環境で依存を入れて起動する。

```sh
python3 -m pip install -r requirements-discord.txt
DOCICH_DISCORD_ENABLED=1 DOCICH_ALLOW_REAL_AI=1 \
  PYTHONPATH=src python3 -m docich.discord_chat
```

停止はその開発プロセスへの通常の終了操作で行う。systemdやdocich配信サービスを再起動しない。Discordへの送信結果が不明な失敗では自動再送しない。

## 検証と未実装

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_discord_chat.py
```

資格情報なしのfake transport/fake Discordで会話・境界を検証する。SDK導入時は実際のClient構築もオフラインで検証する。実Discordとの往復、実LLMの返答品質・料金・遅延は別の受入確認であり、単体テスト成功を代用にしない。

今後の候補は、話題を見て自然に参加/沈黙する判断、連続投稿の束ね処理、明示同意・削除を伴う長期記憶、配信の人物像との共有、VC。初期版はこれらを完成扱いしない。

本番常駐化する前に、runtime registry/manifest、worker health、構造化telemetry、diagnostics、課金/レート上限、所有者用停止操作、canonical deploymentを別PRで統合する。このプロトタイプを本番の必須workerへ登録したり既存サービスから自動起動したりしない。

参考：Discord公式Gateway/Intentsドキュメント（https://docs.discord.com/developers/events/gateway）、discord.py公式ドキュメント（https://discordpy.readthedocs.io/en/stable/）。
