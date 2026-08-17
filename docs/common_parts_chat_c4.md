# コメント応答 / ラジオ共通部品化 C4 — 責務分割と昇格設計 (2026-08-17)

本稿は `docs/common_parts_chat.md` §5 の「C4 昇格時は comment 応答と radio を分けて
責務分割 (生成 / 分類 / 翻訳 / 再生 / 投稿) を先に設計する」を受けた設計である。
TTS C4 (`docs/common_parts_tts_c4.md`) と同じく、**実証済みの部品だけ**を docich
正典へ移し、soviet_now 側を薄いラッパへ置換する。

> **ステータス**: 設計 + C-S2 (AI 出力ガード) を docich 正典へ移植済み (2026-08-17)。
> C-S1 以降の実装は C2 実実行の実証後。参照実行 PoC (`docich chat` / `docich radio`)
> は既に動作 (common_parts_chat.md)。

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

## 5. 残余リスク・次段階

- C-S1 (AI ディスパッチ) の昇格は、モデル認証 (opencode/claude/ollama) の扱いが
  本番依存のため、C2 実証後に別設計する
- コメント翻訳 (C-S4) は字幕翻訳 (docich 正典) との二重管理を避ける設計が必要
- ラジオ生成コンテンツのバックアップ (issue #113) と連携し、docich 側でも
  生成物を保存できるようにするかは C2 実証後に判断
