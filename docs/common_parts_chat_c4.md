# コメント応答 / ラジオ共通部品化 C4 — 責務分割と昇格設計 (2026-08-17)

本稿は `docs/common_parts_chat.md` §5 の「C4 昇格時は comment 応答と radio を分けて
責務分割 (生成 / 分類 / 翻訳 / 再生 / 投稿) を先に設計する」を受けた設計である。
TTS C4 (`docs/common_parts_tts_c4.md`) と同じく、**実証済みの部品だけ**を docich
正典へ移し、soviet_now 側を薄いラッパへ置換する。

> **ステータス**: 設計 + C-S2 (AI 出力ガード) を docich 正典へ移植済み (2026-08-17)。
> C-S1 (AI ディスパッチ) は **docich 正典への「完全移植」ではなく、参照実行ラッパ
> (`docich ai`) として契約を固定** (2026-08-18)。C2 実実行 (docich chat 成功) の実証後。
> 参照実行 PoC (`docich chat` / `docich radio`) は既に動作 (common_parts_chat.md)。

## 1. 責務分割

`broadcast/comment.sh` (3,061 行) + `broadcast/radio_engine.sh` (1,664 行) +
`lib/ai_generate.sh` (998 行) が一体で持つ責務を分割する。

| # | 責務 | 現状 | C4 での扱い | 正典の置き場 |
|---|---|---|---|---|
| C-S1 | AI 呼び出しの共通ディスパッチ (モデル選択・フォールバック・タイムアウト・レート制限バックオフ) | `lib/ai_generate.sh` | **昇格候補 (優先度: 高)**。ゲーム非依存で、comment / radio の両方が使う | `src/docich/ai_generate.py` (docich 正典) |
| C-S2 | AI 出力ガード (思考漏れ・tool protocol・作業メモの除去) | `lib/model_output_guard.py` (127 行) | **昇格候補 (優先度: 最高)**。既に単独 Python で、docich の `captions.py` の厳格パーサ思想と同系 | `src/docich/model_output_guard.py` (docich 正典) |
| C-S3 | コメント分類 (heuristic / JSON 契約 / 編集フォールバック) | `broadcast/comment.sh` | 参照実行のまま (C2 実証後に昇格判断) | soviet_now |
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
