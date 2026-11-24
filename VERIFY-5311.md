# VERIFY-5311 — [半熟英雄] キャンプ帰還失敗: ch10 将軍不明

- 対象: azumag/docich issue #5311 / fingerprint `59ca7f307c7bc9b4` / occurrence `f335c874c4837dfd820c`
- ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1435-recall-dispatch-unattributed`（v1435、現行 main と同一ツリー）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。push・PR はしない）
- 現行 main: `ae9ea33b1`（v1435）。worktree `verify/5311-camp-recall` @ `ae9ea33b1`

## 結論: (b) 設計どおり・修正不要 — RECALL_LIMIT の fail-closed 境界が働いた

本件は `camp_recall_aborted` / `camp_recall_limit` で、stage は `dest`、steps 91、
`input_sent: false` / `picker_closed: false` / `interrupted: false`、fort_* はすべて null。
これは **RECALL_LIMIT（90 観測）に達したときの意図的な fail-closed 中断**であり、
コード上は正常に機能した境界である。#5252 の (a) 判定（fort_status 副上限なし＋ abort 証拠欠落）
とは機構が異なる: fort ルートは `fort_status`/`fort_list`/`fort_menu` の非一致画面で
記録も副上限も持たずに全予算を空転する唯一の経路だったが、本件の `dest` ステージには
マーカー歩行・Y ジャンプ・崖離脱・ボスリダイレクトそれぞれに有界な副経路と
非操作画面の hold（`_recall_hold_or_abort`、RECALL_HOLD_TICK_LIMIT=600）があり、
91 ステップの burn はその有界な副経路の組み合わせがゲーム状態（ボス攻撃・卵戦闘・
イベント会話）の中断を挟んで収束しなかった結果である。abort 後は `mem['uncertain']=True` と
`_arm_recall_skip` が入り、位置の再測定へ安全にフォールバックする。

## 判定の根拠

### 1. abort 経路の特定（検証観点1）

- issue の camp_event: `decision: camp_recall_aborted` / `reason_code: camp_recall_limit` /
  `stage: dest` / `steps: 91` / `input_sent: false` / `picker_closed: false` /
  `interrupted: false` / `target_cell: null` / `general: null`。
- `src/docich/hanjuku/flows/recall.py:1498-1513` — `_camp_recall_step` は
  `state['steps'] += 1` の後 `steps > RECALL_LIMIT (90)` で `_record(mem, 'camp_recall_aborted', ...)`。
  stage が FORT_STAGES でないため fort_* フィールドは追加されず、metric は
  `{'stage': 'dest', 'steps': 91}` のみ。これは stage=dest の abort として正常。
- abort 後: `recall.py:1514-1518` — `_flow(mem).pop('recall')` / `mem['uncertain']=True` /
  `_arm_recall_skip(mem, state)`。安全な fail-closed。

### 2. dest ステージの副有界（#5252 との異同）

#5252（ch9 / stage `fort_status`）は fort ルートの `_fort_step`（`recall.py:2170-2218`）が
非一致画面で `return []` を返し続ける唯一の経路であり、画面ごとの副上限も
証拠記録も持たなかったため (a) と判定された。本件の `dest` ステージ
（`recall.py:1887-2035`）は異なる:

- マーカーと自軍城屋根の距離に基づく歩行（`min(6, abs(dx))` / `min(6, abs(dy))`）。
- 屋根が無い場合の `_camp_recall_picker_fallback`（1 回の blind confirm）または
  `_camp_recall_skipped` ＋ Y ジャンプ fallback（`y_jumps < 2`）。
- 崖隣接時の `_cliff_detour_step`（CLIFF_DETOUR_MAX / CLIFF_DETOUR_STALL で有界）。
- ボスリダイレクト時の `_boss_redirect_dest_step` / `_boss_move_dest_step`
  （`boss_lost < 3` → `_abort_boss_redirect`、BOSS_JUMP_LIMIT=2）。
- 非操作画面（text/battle/egg/monster/attack/defense）は
  `_recall_hold_or_abort` で hold し、RECALL_HOLD_TICK_LIMIT=600 で有界化。
  `recall.py:1459-1485` の text/battle hold は steps を消費しない。

したがって dest ステージには fort ルートのような「副有界の欠如」はない。
91 ステップの burn は上記の有界な副経路がゲーム状態の中断（ボス攻撃 started →
attack_observed → egg_battle → event_text_advance）を挟んで収束しなかった結果であり、
RECALL_LIMIT が意図どおりの最終境界として機能した。

### 3. #5306 との異同（failed vs unconfirmed）

- #5306: `camp_recall_unconfirmed` / stage `await_dispatch` / steps 6 /
  `input_sent: true` / `picker_closed: false` / `status: arrival_unconfirmed`。
  帰還 A の送信 receipt は確認されたが、ピッカーが閉じず（受理・到着未確認）のまま
  6 ステップで dispatch wait が終了した「未確認」ケース。
- #5311: `camp_recall_aborted` / `camp_recall_limit` / stage `dest` / steps 91 /
  `input_sent: false`。宛先ピッカーでの確定（`_request_recall` → await_dispatch）に
  一度も到達せず、90 観測の全体予算を使い切った「失敗（上限到達）」ケース。
- 両者はシグナル（camp_recall_failed vs camp_recall_unconfirmed）、ステージ
  （dest vs await_dispatch）、input_sent（false vs true）、観測数（91 vs 6）がすべて異なり、
  月次監査の分類（`month_audit.py:734-737`）も別系統。#5306 は dispatch wait の
  未確認、#5311 は宛先確定前の全体タイムアウト。

### 4. 既検証シグネチャとの突き合わせ

- #5154（ch8 / fort_status）: (a) 実欠陥。fort ルートの副有界欠如。本件は fort ルートを
  経由していない（stage=dest、fort_* すべて null）ため別機構。
- #5203（ch6 / 同シグネチャ）: (b) 設計どおり。abort が RECALL_LIMIT の有界タイムアウトとして
  働いた。本件も同じ (b) 側に寄る。
- #5252（ch9 / fort_status）: (a) 実欠陥。VERIFY PR #5268 マージ済み。本件は stage が
  dest であり、fort_status の副上限欠如には該当しない。

## 関連

- #5252: ch9 fort_status 副上限なし＋abort 証拠欠落 (a)。本件は dest ルートで別機構。
- #5306: ch10 camp_recall_unconfirmed (dispatch wait 未確認)。本件は failed (limit) で別系統。
- 診断 PR #5175 / #5254: fort ステージ向けの証拠投影（診断のみ、入力・遷移・RECALL_LIMIT は不変）。
  本件の dest abort metric は `{'stage', 'steps'}` のみで証拠が薄いが、dest ステージの副経路は
  有界であり、診断強化は #5175/#5254 の延長として扱える（実装欠陥ではない）。

## 受入条件

- 結論: (b) 設計どおり・修正不要
- 根拠: issue metric (stage=dest, steps=91, input_sent=false) と recall.py の dest ステージ副経路
  (marker walk / Y jump / cliff detour / boss redirect / text-battle hold) の有界性
- #5252 (fort_status) と #5306 (unconfirmed) との異同を明記
- read-only 検証。コード変更なし。コミットまで行い、push・PR はしない
