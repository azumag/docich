# コメント応答 / ラジオ共通部品化 — inventory と参照実行設計案 (2026-08-17)

本稿は docich が soviet_now の broadcast/ 経路を「移動より先に参照」するための
inventory と interface 設計案である (`docs/multi_repo_plan.md` §3 C2 /
`docs/handoff_common_parts.md` §3.7-4)。TTS の `docs/common_parts_tts.md` と同様、
soviet_now は読み取り専用。実装は broadcast/ が 2 週間程度無変更になるのを待ってから
判断する。

> **ステータス**: PoC 実装済み (2026-08-17)。安定条件 (2 週間無変更) は
> ユーザー判断により無視。実装は `src/docich/chat.py` + CLI `chat` / `radio`。
> 検証は dry-run + unit tests のみ (AI 実行・音声再生は実装しない方針)。
> broadcast/ の直近変更は 2026-08-16 (`40d7f1b78` "fix: back off models only on
> explicit rate limits")。

---

## 1. 対象とスナップショット

| 項目 | 状態 |
|---|---|
| soviet_now submodule | `dcb2992` |
| broadcast/ | 直近変更 2026-08-16 (`40d7f1b78`)。合計約 10,000 行 |
| docich | stdlib-only、`:98` / `docich_sink` / `stream.mode="null"` が既定 |
| 本番境界 | 本番 sorengame は soviet_now (`:99`, `soren-runtime.service`) が所有 |

## 2. inventory

### 2.1 `broadcast/comment.sh` (3,061 行) 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | コメント応答生成。バックログ集計、分類 (heuristic/JSON)、翻訳、ゲーム文脈構築、応答生成 (opencode/claude/minimax/ollama)、重複抑制、再生キュー連携 |
| ゲーム固有依存 | なし (ゲーム文脈は引数/状態ファイル経由) |
| 環境変数 | `COMMENT_AGENTS` / `COMMENT_CLASSIFIER_*` / `COMMENT_TRANSLATION_*` / `COMMENT_CODEX_TIMEOUT` / `COMMENT_RESPONSE_RETRY_MAX` / `COMMENT_FAILURE_BACKOFF_*` / `COMMENT_CONTEXT_HISTORY_FILE` / `COMMENT_PLAYED_HASHES_FILE` / `TMP_STATE_DIR` / `ELOOP_LIB_DIR` / `IMPROVE_STATE_FILE` / `GACHA_COMPLETED_USERS_FILE` ほか多数 |
| 外部コマンド | bash, python3 (多), opencode, claude, ollama, curl, awk, ps, kill, say_enqueue (内部) |
| 入出力ファイル | `tmp/.comment_queue/played_hashes.txt`、`tmp/.twitch_chat/comment_gen.pid`、`tmp/state/*`、`CODEX_BUG_QUEUE_DIR` ほか |
| キュー・ロック | `COMMENT_BATCH_INFLIGHT_FILE` によるバッチ inflight 判定、backoff ファイル |
| AI 依存 | opencode / claude / minimax / ollama のいずれか。認証とモデル設定は `.env` / 環境変数 |
| セキュリティ上の注意 | プロンプトにチャット内容を含む。生成テキストは `_ai_guard_model_output` でガード。docich 側はプロンプト/認証を作らない |
| docich から参照実行できるか | 関数ライブラリであり単体入口がない。将来は `scheduler.sh` 経由か、`comment.sh` を source して特定関数のみ呼ぶ形を検討 |
| 今は触らない理由 | 直近で活発に変更中。応答生成は本番チャットに直結するため、参照契約の確定が先 |

### 2.2 `broadcast/comment_lib.sh` (360 行) 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | コメント応答の共通補助 (再生済みハッシュ、backoff、メタ情報) |
| docich からの参照 | 可 (source して関数を呼ぶ) だが、`comment.sh` との二重管理回避のため comment.sh 経由を優先 |

### 2.3 `broadcast/radio_engine.sh` (1,664 行) 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | ラジオ生成の AI 実行ラッパ (opencode/claude/minimax/ollama)、パース、サニタイズ、生成&再生 |
| ゲーム固有依存 | なし (ラジオはゲーム非依存コンテンツ) |
| 環境変数 | `RADIO_AGENTS` / `RADIO_MAIN_AGENT` / `RADIO_MAIN_FALLBACK` / `RADIO_OPENCODE_*` / `RADIO_CLAUDE_*` / `RADIO_QUALITY_CHECK_ENABLED` / `RADIO_FACT_CHECK_MIN_CHARS` / `IMPROVE_LOCK_FILE` / `TMP_STATE_DIR` / `MINIMAX_*` / `OLLAMA_BASE_URL` ほか |
| 外部コマンド | opencode, claude, ollama, python3, timeout, curl |
| 入出力ファイル | `tmp/state/*`、`/tmp/eloop_radio_raw_*`、`PAST_JIJI_URL_HASHES` / `PAST_NEWS_*` ほか |
| キュー・ロック | `IMPROVE_LOCK_FILE` / `rate_limit_backoff` / `improve_state.json` による defer、`AI_GENERATION_QUEUE_ENABLED` |
| 並行実行時の挙動 | improve 実行中は defer、rate limit backoff 対応、AI 生成は直列化 |
| docich から参照実行できるか | 可 (関数単位) だが、生成・再生・チャット投稿が一体。docich 側は `--dry-run` での検証を想定 |
| 今は触らない理由 | radio 一式 (corners/news/persona/state/quality/factcheck/themes/celebration) と密結合。参照契約の確定が先 |

### 2.4 `broadcast/scheduler.sh` (814 行) 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | コメント/ラジオ/その他の定期実行スケジューラ |
| docich からの参照 | 将来 `docich chat` の実行入口候補。ただし実行時刻・優先度・AI 並行制御が一体で、PoC での直接実行は行わない |

## 3. 責務境界 (設計案)

| 責務 | docich | soviet_now |
|---|---|---|
| コメント応答生成の「正典」 | 参照実行のみ (将来 C2 で昇格候補) | `broadcast/comment.sh` ほか |
| ラジオ生成の「正典」 | 参照実行のみ | `broadcast/radio_engine.sh` ほか |
| AI 実行 (opencode/claude/minimax/ollama) | しない (PoC)。認証・モデル選択は soviet_now 側 | 所有 |
| チャット投稿 | **しない (PoC 既定)**。将来 `docich chat --chat` は C2 で設計 | 所有 (`outbound_queue.sh` 経由) |
| 音声再生 | `say_enqueue.sh` 参照実行 (common_parts_tts.md) | 所有 |
| 本番配信 | `stream.mode="null"` 既定 | `soren-runtime.service` が所有 |

## 4. 推奨インターフェース (PoC 設計案)

候補比較 (common_parts_tts.md §4 と同じ基準):

| 候補 | 評価 |
|---|---|
| `docich chat <game> -f <text>` / `docich radio <game> --topic <text>` | ★ 推奨。`bin/docich` の自然な拡張。TTS の `say` と同じ参照実行パターン |
| 内部モジュール + CLI ラッパ | 実装方向それ自体。`src/docich/chat.py` / `src/docich/radio.py` に argv/env 構築を置く |
| adapter として扱う | 不採用。TTS と同じ理由 |

### 4.1 `docich chat` / `docich radio` の契約 (PoC 実装済み)

```
docich chat <game> [--source twitch|youtube] [--dry-run]
docich radio <game> --topic <text> [--dry-run]
docich radio <game> --prompt-file <path> [--corner NAME] [--dry-run]
```

実装:

- `src/docich/chat.py` に参照実行の argv/env/cwd 構築を置く。allowlist は
  `broadcast/comment.sh` の `generate_comment_response` と
  `broadcast/radio_engine.sh` の `_radio_generate_and_play` のみ
- 実行は固定ラッパ `broadcast_ref.sh` (一時生成) 経由。ラッパは `eloop_lib.sh` を
  source して本番と同じ source 順で関数を呼ぶ。ユーザーテキストは argv に渡さず、
  `--topic` は一時プロンプトファイルへ、`--source` / `--corner` は allowlist /
  安全トークン検証 (`[A-Za-z0-9._:-]{1,128}`) を通す
- 既定は **チャット投稿なし**。`OUTBOUND_CHAT_QUEUE_DIR` を一時ディレクトリへ向け、
  本番 Twitch チャットへ投稿しない (common_parts_tts.md §4.2-5 と同じ)
- `--dry-run` は argv/env/cwd/function を表示する検証 (実コマンドは実行しない)
- 実実行は明示許可が必須: comment は `DOCICH_ALLOW_REAL_COMMENT=1`、radio は
  `DOCICH_ALLOW_REAL_RADIO=1`。無ければ TtsError 相当で拒否 (AI 実行・音声再生を含むため)
- `--topic` と `--prompt-file` はどちらか一方のみ。`--game-num` は 0 以上、score は
  安全トークン検証済み
- 字幕・TTS との連携は `say_enqueue.sh` 参照実行側に委ねる (common_parts_tts.md)

## 5. 残余リスク・次段階

- broadcast/ は直近変更あり (2026-08-16) だが、ユーザー判断で安定条件を無視して
  PoC 実装済み。**実実行 (AI 生成) はまだ一度も行っていない**
- AI 実行は認証・コスト・並行衝突のリスクがある。実実行は
  `DOCICH_ALLOW_REAL_COMMENT` / `DOCICH_ALLOW_REAL_RADIO` の明示で許可する設計
  だが、初回検証は dry-run と、本番キューを汚さない別 checkout で行う
- チャット投稿は本番チャットを汚す可能性がある。投稿経路の実装は C2 後半として別途設計する
- `radio_engine.sh` は improve と defer 連携 (`IMPROVE_LOCK_FILE`) を持つ。参照実行時に
  本番 improve と衝突しない設計が必要
- C4 昇格時は、comment 応答と radio を分けて責務分割 (生成 / 分類 / 翻訳 / 再生 / 投稿) を先に設計する
