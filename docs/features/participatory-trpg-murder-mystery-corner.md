# 参加型 TRPG / マーダーミステリー コーナー — 設計提案

> **⚠️ Superseded（2026-10-18 統合判断）**: 本提案のオフライン中核（`trpg_corner.py`、
> PR #2481）は、#684 の統合判断により汎用ロジック（投票集計・実行台帳・fail-closed検証）が
> `mystery_participation.py` / `mystery_corner.py` へ取り込まれ、**モジュールは削除済み**です。
> 実装の正本は [`docs/features/mystery-corner.md`](mystery-corner.md)（決定論ケースエンジン＋
> Twitchアンケート/チャンネルポイント予想）。本docは設計経緯の記録として保持します。
> シナリオファイル方式やチャット `!1`〜`!4` / `!accuse` 参加は本番には未導入（将来の参加
> レイヤ候補）。

- Status: ~~Proposal（起案）~~ → **Superseded**（統合判断 2026-10-18）。production の corner catalog は変更しない。
- 関連 Issue: [azumag/docich#684](https://github.com/azumag/docich/issues/684)（参加型？TRPGやマーダーミステリー）
- 関連実装（既存の参加型・合成ビュー基盤）:
  - `src/docich/corner_catalog.py` / `corner_rotation.py` / `corner_adapters.py`（24時間ローテーション）
  - `src/docich/tsuitate_corner.py` / `src/docich/tsuitate_view.py`（合成ビュー型コーナーの先例）
  - `src/docich/paper_corner*.py`（読み取り専用ビュー + 状態機械の先例）
  - `games/soviet_now/twitch_chat.sh` / `twitch_chat_daemon.sh` / `viewer_chat_monitor.sh`（視聴者チャット入力）
  - `games/soviet_now/lib/prediction_round.py`（視聴者参加ラウンドの先例）
  - `docs/features/yosou-oh-corner.md`（同種の設計提案テンプレート）

## 0. 何を作るか

配信の番組ローテーションに、**視聴者がチャットで参加する TRPG / マーダーミステリー**のコーナーを追加する。

- Soren（AI）が GM / ナレーターを担当し、シーン描写・NPC・判定を音声と画面で進行する。
- 視聴者は Twitch チャットで「行動選択」「投票」「推理提出」を行い、結果が次シーンへ反映される。
- 画面は 960×540 の読み取り専用合成ビュー（既存 weather / paper / tsuitate と同じ方式）で、
  現在のシーン・選択肢・参加者・残り時間・推理メモを表示する。
- ゲーム本体・配信 encoder・共通音声基盤は変更しない（AGENTS.md のゲーム切替原則に従う）。

本ドキュメントは #684 の「参加型？TRPGやマーダーミステリー」を、実装可能な設計提案へ落とし込む。
**タイトルの疑問符（？）のとおり、参加形態・ジャンルのどちらを主軸にするかは未確定**であり、
本提案は両方を包含する共通基盤を先に定義し、ジャンル固有ロジックを後段に分離する。

## 1. 参加形態の整理（未確定・オーナー判断）

| 形態 | 説明 | 難易度 | 備考 |
| --- | --- | --- | --- |
| A. 選択肢投票型 | シーンごとに 2〜4 の選択肢を提示、チャットで投票、多数決で進行 | 低 | 最初の MVP 候補 |
| B. 自由入力型 | 視聴者が自由文で行動を提案、AI が解釈して反映 | 高 | LLM コスト・安全性の検討が必要 |
| C. 推理提出型（マーダーミステリー） | 各視聴者が「犯人」「トリック」「動機」をチャットで提出、締切後に集計・発表 | 中 | 正解判定ロジックが必要 |
| D. 役割分担型 | 視聴者が NPC / 探偵 / 容疑者などの役を担い、セリフをチャットで送る | 高 | 司会（Soren）の調整負荷大 |

**本案の推奨**: MVP は **A（選択肢投票型）+ C（推理提出型）の組み合わせ**。
TRPG の進行は A で、クライマックスの推理フェーズは C で行う。
B / D は将来拡張として位置づける。

## 2. 既存基盤との関係

- **コーナーローテーション**: `corner_catalog.py` の `Corner` データクラスに新 adapter `trpg` を追加。
  game id は `trpg-view`（合成ビュー専用の仮想ゲーム。実行バイナリ不要）。
- **GameSwitch / program slot**: 他コーナーと同じ program slot 排他。表示時間は上限つき（例: 5〜10 分）で
  自動終了し、既存の GameSwitch 復帰経路へ戻す。
- **チャット入力**: `twitch_chat_daemon.sh` / `viewer_chat_monitor.sh` が既にチャットを収集している。
  本コーナーはそれらのログ/イベントを読み取り専用で消費する（daemon 自体は変更しない）。
- **音声**: 既存の TTS（`voicevox_tts.sh` 等）へ原稿を enqueue するだけ。新規 TTS 基盤は作らない。
- **状態永続**: `<state_dir>/trpg_corner/` 配下に JSON で保存（atomic write + 共有 lock）。
  既存の `prediction_leaderboard.py` / `paper_corner` の永続化パターンに倣う。

## 3. 状態機械（シーン進行）

```
IDLE -> SCENE (描写) -> CHOICE (選択肢提示・投票受付) -> RESOLVE (集計・結果) -> SCENE ...
                                                              \-> CLIMAX (推理提出) -> REVEAL (正解発表) -> END
```

- `SCENE`: Soren がシーン描写を読み上げ、画面に背景テキストを表示。
- `CHOICE`: 選択肢を 2〜4 個提示。投票受付窓（例: 60 秒）を開ける。
  チャットの `!1`〜`!4`（または絵文字）を投票として集計。
- `RESOLVE`: 多数決の結果を画面・音声で発表し、次の SCENE へ。
- `CLIMAX`: 推理提出窓（例: 120 秒）。`!accuse <名前> <トリック>` 形式。
- `REVEAL`: 正解発表・参加者スコア・MVP 表示。
- `END`: 集計を保存し、GameSwitch 復帰。

各シーンはタイムアウトで自動進行（暗黙に試合を強制終了しない原則に従い、未完了は明示して待機/失敗）。

## 4. データ契約（台帳）

### 4.1 シナリオ定義（静的）

`config/trpg/scenarios/<scenario_id>.json`（または TOML）:

```json
{
  "id": "murder-001",
  "title": "洋館の殺人",
  "genre": "murder_mystery",
  "scenes": [
    { "id": "s1", "text": "...", "choices": ["...", "..."], "next": {"1": "s2a", "2": "s2b"} }
  ],
  "climax": { "suspects": ["A", "B", "C"], "answer": "B", "trick": "..." },
  "duration_minutes": 8
}
```

- シナリオは **事前作成・レビュー済み**のもののみ使用（AI 即興生成は MVP 範囲外）。
- 文字列長・選択肢数・シーン数に上限を設ける（入力検証は fail-closed）。

### 4.2 実行時状態（動的）

`<state_dir>/trpg_corner/state.json`:

```json
{
  "schema": 1,
  "scenario_id": "murder-001",
  "phase": "CHOICE",
  "scene_id": "s3",
  "started_at": "...",
  "votes": { "1": ["userA", "userB"], "2": ["userC"] },
  "accusations": [{ "user": "userA", "suspect": "B", "trick": "..." }],
  "scores": { "userA": 10 }
}
```

- 冪等: 同一ユーザーの再投票は上書き（最後の1票のみ有効）。
- 参加者情報は Twitch が公開する `id / login / 表示名` のみ。トークン・IP は保存しない。

### 4.3 記録台帳（永続）

`<state_dir>/trpg_corner/history.jsonl` に 1 回のコーナー実行を 1 行で追記:

```json
{ "scenario_id": "...", "started_at": "...", "ended_at": "...", "participants": 12,
  "votes_total": 45, "accusations": [...], "mvp": "userA", "outcome": "completed" }
```

- 最大件数・サイズ上限・古い順の間引き（`prediction_leaderboard.py` と同パターン）。

## 5. 画面表示（合成ビュー）

- 960×540 の読み取り専用ビュー。`WeatherCornerAdapter` / `TsuitateCornerAdapter` と同列の合成 view。
- 表示要素: タイトル / シーンテキスト / 選択肢リスト / 投票バー / 残り時間 / 参加者数 / 推理メモ欄。
- 文字は既存フォント（`hanjuku_font.py` 等）を流用。絵文字は使わない（環境依存回避）。
- GameSwitch の program slot に乗るため、encoder 再起動は不要。

## 6. ローテーション統合

- catalog 例（**既定無効・手動予約可**で提案）:

  ```toml
  { id = "trpg-corner", adapter = "trpg", game = "trpg-view", enabled = false, manual_only = true }
  ```

- `live_eligible` は `false`（既存制約）。有効化はオーナー承認後。
- required executables: `python3` のみ（合成ビューのため retroarch 等不要）。

## 7. 安全性・プライバシー

- **内容審査**: シナリオは事前レビュー済みのもののみ。AI による即興生成は MVP 範囲外。
- **チャット入力の扱い**: 自由文を受け付ける C/D 形態では、不適切語フィルタ・文字数制限・
  レート制限を設ける。MVP（A+C）では `!1`〜`!4` と `!accuse` の構造化入力のみなのでリスク低。
- **個人情報**: 表示名は Twitch 公開情報のみ。opt-out 申請手段を用意（将来）。
- **障害時**: 進行不能な場合は `failed` として明示し、GameSwitch 復帰経路へ戻す（暗黙の強制終了は禁止）。

## 8. MVP

- [ ] 状態機械 + 台帳のオフライン中核（`src/docich/trpg_corner.py`）＋ `tests/test_trpg_corner.py`
- [ ] シナリオ 1 本（`config/trpg/scenarios/murder-001.json`）
- [ ] チャット投票の集計（構造化入力 `!1`〜`!4`）
- [ ] 推理提出 `!accuse` の集計・正解判定
- [x] `trpg` adapter ＋ 合成ビュー (PR-3)
- [ ] ローテーション登録（既定無効）
- [ ] 読み上げ原稿（Soren GM ナレーション）
- [ ] overlay / Twica 表示

## 9. 実装ステップ（PR 分割案）

1. **PR-1**: オフライン中核（状態機械・台帳・検証）＋ 本設計 doc ＋ テスト。
2. **PR-2**: チャット投票/推理提出の集計配線（既存 chat daemon の読み取り専用消費）。
3. **PR-3**: `trpg` adapter ＋ 合成ビュー ＋ ローテーション登録（既定無効）。
4. **PR-4**: 読み上げ原稿・overlay、オーナー承認後の有効化と VM 反映。

## 10. 未確定・オーナー判断が要る点

1. ジャンル: TRPG（継続キャンペーン）/ マーダーミステリー（1回完結）/ 両方（本案は両方を包含する共通基盤を先に定義）。
2. 参加形態: A/B/C/D のどれを主軸にするか（本案は A+C を推奨）。
3. 投票窓・推理窓の秒数、コーナー表示時間（例: 8 分）。
4. シナリオ作成の責任者・レビュー体制（AI 生成 vs 人間執筆）。
5. 視聴者表示名の opt-out / 不適切名の扱い。
6. スコアリング・MVP の基準（的中 / 参加回数 / 投票多数派との一致など）。
7. 既存の `prediction_leaderboard.py` とスコアを統合するか分離するか。

## 11. 検証

```sh
env -u PYTHONPATH python3 -m pytest -q tests/test_trpg_corner.py
```

- Twitch・ゲーム・本番 VM への接続は行わない（設計段階）。
- 本提案は production catalog を変更しない。有効化はオーナー承認後。
