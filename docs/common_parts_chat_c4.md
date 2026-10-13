# コメント応答 / ラジオ共通部品化 C4 — 責務分割と昇格設計 (2026-08-17)

本稿は `docs/common_parts_chat.md` §5 の「C4 昇格時は comment 応答と radio を分けて
責務分割 (生成 / 分類 / 翻訳 / 再生 / 投稿) を先に設計する」を受けた設計である。
TTS C4 (`docs/common_parts_tts_c4.md`) と同じく、**実証済みの部品だけ**を docich
正典へ移し、soviet_now 側を薄いラッパへ置換する。

> **ステータス**: 設計 + C-S2 (AI 出力ガード) と C-S1 native dispatch の
> 初回移植を docich 正典へ反映済み (2026-09-22)。
> 旧 §5.1〜§5.3 は、native化前の参照実行wrapperを記録した履歴として残す。
> #829 PR-1で `src/docich/llm/` へ段階移植し、`docich ai` はnative dispatchへ
> 切り替えた。C2 chat/radioはまだlegacy互換経路である。
> 参照実行 PoC (`docich chat` / `docich radio`) は既に動作 (common_parts_chat.md)。

## 1. 責務分割

`broadcast/comment.sh` (3,061 行) + `broadcast/radio_engine.sh` (1,664 行) +
`lib/ai_generate.sh` (998 行) が一体で持つ責務を分割する。

| # | 責務 | 現状 | C4 での扱い | 正典の置き場 |
|---|---|---|---|---|
| C-S1 | AI 呼び出しの共通ディスパッチ (モデル選択・フォールバック・タイムアウト・レート制限バックオフ) | `lib/ai_generate.sh` | **昇格候補 (優先度: 高)**。ゲーム非依存で、comment / radio の両方が使う | `src/docich/ai_generate.py` (docich 正典) |
| C-S2 | AI 出力ガード (思考漏れ・tool protocol・作業メモの除去) | `lib/model_output_guard.py` (127 行) | **昇格候補 (優先度: 最高)**。既に単独 Python で、docich の `captions.py` の厳格パーサ思想と同系 | `src/docich/model_output_guard.py` (docich 正典) |
| C-S3 | コメント分類 (heuristic / Jev / 正規化・英語安全化) | `src/docich/comment_classifier/` (旧: `broadcast/comment.sh` + `lib/comment_classifier_jev.py`) | **docich へ昇格 (2026-09-23, #882/#829)**。soviet_now は呼び出し 1 行のみ | `src/docich/comment_classifier/` (docich 正典) |
| C-S4 | 翻訳 (英訳) | `broadcast/comment.sh` + `lib/comment_bilingual.py` | 字幕翻訳は `src/docich/captions.py` が正典。コメント翻訳は C2 実証後に判断 | soviet_now (コメント) / docich (字幕) |
| C-S5 | ラジオ生成 (テーマ・ニュース・コーナー別プロンプト) | `broadcast/radio_*.sh` | 参照実行のまま。ゲーム非依存だが、生成コンテンツの運用が一体 | soviet_now |
| C-S6 | 再生キュー連携 (say_enqueue / deferred) | `broadcast/comment_lib.sh` + `radio_state.sh` | TTS 側 (`docich say`) の契約に委ねる。docich は追加キューを作らない | soviet_now 所有 |
| C-S7 | チャット投稿 | `lib/outbound_queue.sh` | **docich からは投稿しない (C2 PoC 既定)**。`docich chat --chat` は設計のみ | soviet_now 所有 |

## 2. 昇格方針

### 2.1 今回設計するもの (C-S2 が最優先)

- `src/docich/model_output_guard.py`: `lib/model_output_guard.py` をそのまま移植
  (stdlib-only、stdin → stdout のフィルタ)。**実装済み (2026-08-17)**。
  CLI `docich ai-guard` を追加し、stdin を読んで `extract_final_text` の結果を
  stdout へ出す。soviet_now の `_ai_guard_model_output` は将来薄いラッパで
  docich へ委譲できる。
- 使いどころ: `docich chat` / `docich radio` の実実行時に、生成テキストを
  soviet_now の `ai_generate.sh` 経由ではなく docich 側でガードしたい場合。
  まずは docich 内の検証・テストから使い、soviet_now 側は薄いラッパへ。

### 2.2 今回設計しないもの

- AI ディスパッチ (C-S1) は、モデル認証・プロバイダ設定が soviet_now の `.env` に
  依存するため、C2 実実行の実証後に昇格判断する。
- ラジオ生成 (C-S5) はプロンプト運用が一体で、参照実行のまま。
- チャット投稿 (C-S7) は本番チャットを汚すリスクがあるため、PoC では投稿しない。

## 3. 安全条件 (C-S2 昇格時)

1. ガードは stdin → stdout の純フィルタ (副作用なし)。モデルを呼ばない
2. `docich ai-guard` は `--dry-run` 不要 (実行しても出力のみ)。ただし argv は
   受け取らず stdin 経由で入力
3. パーサは既存 `model_output_guard.py` の保守的契約をそのまま維持する
   (未マーク出力の破棄・フォールバックは呼び出し側)
4. soviet_now 側は `_ai_guard_model_output` を docich 呼び出しへ置換する場合のみ
   薄いラッパにする (別 PR、ユーザー承認後)

## 4. 検証計画

- unit tests: `tests/test_model_output_guard.py` (127 行ガードの既存 テスト) を
  docich 側に移植し、全件緑を確認
- 実施済み (2026-08-17): docich 側 `tests/test_model_output_guard.py` 9 件 +
  全体 464 件全緑。CLI `docich ai-guard` は `<analysis>` を除去して `<final>` を出力
- `docich ai-guard` の CLI dry-run (入力は stdin なので、実際にガードを通す)
- C2 実実行 (AI 生成) は本番キューを汚さない別 checkout + `--dry-run` から開始

## 5. C-S1: docich 正典ではなく参照実行ラッパ (`docich ai`)

### 5.1 前提と判断

soviet_now `lib/ai_generate.sh` (1,009 行) は、モデル選択・フォールバック・バックオフ・
レート制限検出に加え、**生成キュー排他・opencode XDG state 同期・opencode run lock 掃除**など
本番運用に密結合している。これらを docich 側で「完全移植」すると:

- opencode の認証同期 (`OPENCODE_AUTH_SOURCE` → XDG data) と、本番 worker が共有する
  run lock / 生成キュー排他を docich から独立させて再実装することになり、多数同時実行の
  本番 worker と lock を取り合う危険がある
- `ai_generate.sh` と Python 実装の二重管理になり、common_parts の原則
  「移動より先に参照」「二重管理を避ける」に反する

したがって C-S1 は、TTS (`docich say` → `say_enqueue.sh`) と同型の「参照実行ラッパ」で
契約を固定する。docich はディスパッチ実装をコピーせず、soviet_now の `ai_generate_list` を
安全に呼ぶ。soviet_now 側は読み取り専用のまま。

### 5.2 実装 (2026-08-18)

- `src/docich/ai_generate.py`: `ai_generate_list` を参照実行する安全ラッパ
  - allowlist 関数は `ai_generate_list` のみ (backoff 付きリスト型、本番の実質口)
  - ラベルは `COMMENT` / `RADIO` で始まる安全トークンに限定
  - エージェントリストは英数字 / `.` `_` `:` `-` のカンマ区切りに限定
  - プロンプトは一時ディレクトリへ複製して渡す (任意パスをコマンドラインに載せない)
  - `AI_BACKOFF_DIR` / 生成キュー lock / opencode run lock を一時ディレクトリへ向けて
    本番の状態・排他を汚さない
  - validator 引数は渡さない (プロンプト形式の検証は呼び出し元の責務)
  - 既定は dry-run。実実行は `DOCICH_ALLOW_REAL_AI=1` で明示許可
- CLI: `docich ai <game> --label <COMMENT|RADIO...> --agents <list> --prompt-file <path>
  [--timeout SEC] [--dry-run]`
- 検証: `tests/test_ai_generate.py` 8 件全緑、全体 474 件中失敗は既知の socket 環境要因 6 件のみ。
  CLI dry-run で argv/env/cwd の構築を確認済み

### 5.3 残課題

- 実実行 (`DOCICH_ALLOW_REAL_AI=1`) は、本番 `.env` (OPENCODE_GO_API_KEY 等) を source した
  環境でのみ認証が通る。docich にはシークレットを埋め込まず、実行時にユーザー側で
  env を渡す (common_parts_chat.md §4.2 と同じ)。
- ラジオ生成 (C-S5) は `ON_AIR_SCRIPT_START` 形式契約により、`docich ai` で直接プロンプトを
  渡すより本番の `radio_persona.sh` 組立を参照実行する方が正しい (common_parts_chat.md §4.2)。
- コメント翻訳 (C-S4) は字幕翻訳 (docich 正典) との二重管理を避ける設計が必要。
- ラジオ生成コンテンツのバックアップ (issue #113) と連携し、docich 側でも生成物を
  保存できるようにするかは判断保留。

### 5.4 C-S1 PR-1 native dispatch (2026-09-22)

上記5.1〜5.3の参照実行wrapper記述はPR-0時点の履歴である。#829 PR-1で
`src/docich/llm/` を正典として追加し、`src/docich/ai_generate.py` は
`games/soviet_now` をsource/execしないnative ordered fallbackへ切り替えた。

- `docich ai` はゲーム名なしでも実行できる（旧ゲーム名は互換引数として無視）。
- COMMENT/RADIOのtimeout、provider allowlist、rc=79 backoff、failure streak、
  generation lane、OpenCode lock、real-run gateを移植した。
- `corner_improve.py` と `trading/ai_text.py` も同じtyped dispatcherを使う。
- `docich chat` / `docich radio`、分類、翻訳、delivery、ラジオpromptはまだlegacy
  compatibility経路であり、本節の変更だけでは#829完了とはしない。
- 実API・本番queue・VMの反映は行わず、mock/focused testと後続release gateで分離する。

詳細な変更契約と未完了境界は `docs/plans/829-pr1-native-llm-dispatch.md` を参照。

## 6. C-S4 / C-S5 の判定 (2026-08-19)

### 6.1 C-S4: コメント翻訳は soviet_now 所有のまま。第三実装を作らない

コメント/ラジオの英訳は、soviet_now の `_comment_build_translation_prompt` +
`_comment_generate_translation`(comment.sh) + `comment_bilingual.py` が一体で持つ。
docich 側には、これを並列で再実装する翻訳スタックを**作らない**:

- 字幕翻訳は docich 正典 `src/docich/captions.py` の `TranslationRuntimeClient`
  (loopback 限定・厳格 JSON・`{"translations":[...]}`) が正。
- コメント間訳は段落単位の自然な英語が必要で、字幕の 32×2 CEA-608 契約とは意図的に
  別。docich は `docich chat <game>` の参照実行で `generate_comment_response` を呼び、
  翻訳を含む全経路を soviet_now に委ねる (二重管理回避)。
- 両者が共有するのは「LiteLLM ローカル (127.0.0.1:4100) を経由するモデルチェーン」
  だけ。本番実測 (2026-08-19): `COMMENT_TRANSLATION_AGENTS` 既定 =
  `COMMENT_AGENTS` = `local → codex:deepseek-v4-flash-free →
  codex:openrouter/free → codex:amd-token-factory-deepseek-v4-flash →
  codex:deepseek-v4-flash → codex:minimax-m3`。字幕翻訳
  `DOCICH_CC_TRANSLATION_MODELS=deepseek-v4-flash,minimax-m3`。
  (2026-09-23 追記: docich の既定チェーンへ `opencode:mimo-v2.6-flash-free` /
  `opencode-go:mimo-v2.6-flash` を muse の後・deepseek の前に追加したが、
  VM の `.env` / `core/config.sh` は未反映のため上記の本番実測値はそのまま。)

docich 実装は変更なし。将来、仕様を詰める場合のみ `docich translate` (段落向け厳格
クライアント) を検討するが、`docich chat` の参照実行が既に覆うため現時点では不要。

### 6.2 C-S5: ラジオ原稿バックアップは既に参照実行で接続済み。別コピー不要

soviet_now の `_radio_backup_script`(radio_state.sh) が、再生完了時に原稿を
`backups/radio_scripts/<date>/` へ `.history` + `.meta.json` と共に自動退避する
(PR #112、本番反映済み)。

- `docich radio` は参照実行で `_radio_generate_and_play` を呼ぶため、バックアップは
  実行時に `cd` する checkout の cwd に追従する (`date_dir="backups/radio_scripts/..."`
  は相対パス):
  - 隔離実行 (既定) → 参照用の別 checkout/別 clone の cwd 配下へ退避。本番を汚さない。
    (`docich chat` は cwd をサブモジュールルートにして実行する)
  - 実実行 (`DOCICH_ALLOW_REAL_RADIO=1`) → 本番 `/home/ubuntu/soren` の
    `backups/radio_scripts/` へ退避 (既存動作のまま)。
- したがって docich 側にバックアップを独自実装しない。「判断保留」→ **解決**。

### 6.3 C-S3: コメント分類は docich の共通部品 (2026-09-23 改訂, #882 / #829)

**旧判断 (2026-08-19) を撤回する。** 旧判断は「heuristic の語彙がソ連ゲーム専用なので
昇格できる汎用部品が無い」として soviet_now 所有・参照実行のままとした。しかし実際には
分類器 (heuristic + Jev + 正規化) が soviet_now に置かれたことで、provider transport・
endpoint・credential の正典まで soviet_now 側に引きずられ、docich の共通 semantic
decision 基盤 (#882) と二重化した。オーナー判断 (2026-09-23): **分類器は共通部品として
docich 側だけが持つ。soviet_now に分類器の影響を残さない。**

- 正典: `src/docich/comment_classifier/`
  - `heuristic.py`: 旧 `_classify_comments_heuristic` / `_normalize_comment_classification_json` /
    `_comment_enforce_english_safety` / `comment_bilingual.looks_like_english` の挙動保存移植。
    soviet_now `f2c20234` の shell 出力を `tests/fixtures/comment_classifier_heuristic_golden.json`
    に固定し、byte 一致をテストする。
  - `jev.py`: 旧 `lib/comment_classifier_jev.py` の用途層 (固定 rubric・本文限定の投影・
    閾値・通知保護・cooldown・sanitized metrics)。HTTP は `docich.semantic_decision` のみ。
  - 入口: `bin/docich-comment-classify <file>` (= `python -m docich.comment_classifier`)。
    stdout は従来と同じ JSON 配列契約 (index/user/comment/category/is_english)。
- soviet_now 側は `_classify_comments` から上記入口を呼ぶだけにし、分類ロジック・
  Jev 実装・rubric は削除する (別 PR)。コメント処理全体の docich 移行 (#829) で
  この呼び出しも消える。
- ゲーム特化語彙 (ロシア/ソ連/盤面 等) は現状 heuristic に残る。これは #829 の
  GameContextProvider 導入時に purpose 設定へ切り出す対象であり、今回は挙動を変えない。
- 旧 AI 生成による分類経路 (`COMMENT_CLASSIFIER_AI_ENABLED=1`、45/90 秒の edit/stdout 契約)
  は移植しない。本番は `COMMENT_CLASSIFIER_BACKEND=jev` でこの経路を通らず、
  Jev 無効時の既定も heuristic である。

### 6.4 進め方のまとめ (C-S3〜C-S7)

| # | 責務 | 判定 (2026-08-19) |
|---|---|---|
| C-S3 | コメント分類 | **docich 所有** (2026-09-23 改訂)。soviet_now は呼び出しのみ |
| C-S4 | コメント翻訳 | soviet_now 所有 (参照実行)。第三実装を作らない |
| C-S5 | ラジオ生成 | soviet_now 所有 (参照実行)。バックアップは参照実行で接続済み |
| C-S6 | 再生キュー | `docich say` の契約に委ねる。docich は追加キューを作らない |
| C-S7 | チャット投稿 | docich からは投稿しない |
