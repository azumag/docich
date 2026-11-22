# IMPLEMENT-5134 — [半熟英雄] キャンプ帰還失敗: ch6 将軍不明

ブランチ: `fix/5134-camp-recall-map-stall`（main `34be26425`＝v1392 から作成、push・PR なし）
BOT_VERSION: `hanjuku-chart-v1397-recall-map-hold`
（v1393 は #5141/#5149、v1394 は #5128、v1395 は #5137、v1396 は #5020 の作業用に予約されているため、本 PR は v1397 を採用）

検証結論: `verify/5134-camp-recall-failed` ブランチの `VERIFY-5134.md` —
(b) 別経路の実欠陥。#5134 の 91 steps は `to_camp` で 'map' kind の
フレームによってのみ消費されており、PR #5133 (v1389) の hold は
'text'/戦闘系 kind にしか届かない。`recall.py` の `state.pop('hold_since')`
→ `steps += 1` → `to_camp` ナビの経路で、カーソル未検出の
`return []`（加算済み・入力なし・進捗なし）か、戦闘ごとのカーソル
位置リセットによる非収束ナビかが予算を燃やす。

採用方針（DSH 判断済み）: 案1（cursorless 'map' の非加算 hold）＋
案2（戦闘復帰時の steps リセット）＋案4（回帰テスト）。
案3（nudge 許可）は実施しない（保留）。

## 変更内容

### 1. `to_camp` のカーソル未検出 'map' フレームを非加算の hold へ（案1）

`src/docich/hanjuku/flows/recall.py` `_camp_recall_step`:

- 'text' hold ブロックの直後、`state.pop('hold_since')` の手前に
  `screen.kind == 'map' and stage == 'to_camp' and not _cursor(screen)`
  の hold 分岐を追加。`_recall_hold_or_abort()` に委ねて
  `hold_since` を打刻し、`RECALL_HOLD_TICK_LIMIT`（600 tick）を
  超えた場合のみ既存の `camp_recall_aborted` + `_arm_recall_skip()`
  経路を取る。
- pop の手前に置いたのは、連続する cursorless フレームが毎回
  `hold_since` を打ち直して有界化が効かなくなるのを防ぐため。
  カーソルが読めたフレームは従来どおり pop → `steps += 1` で hold を
  終了する。
- 戻り値は `[]`（`return None` ではない）。`to_camp` 中の 'map'
  フレームは orchestrator で recall が前景所有するため、保持して無入力
  を返す。abort 発火時は `_recall_hold_or_abort` の返す `[]` をそのまま
  返す。
- **blind map の除外**: `not screen.header and not screen.text` の
  HUD-less フレーム（map.py の blind 章間インターミッション条件と同じ）
  は hold しない。従来どおり加算・`camp_missing_reads` 経路で帰還を
  早期に手放し、`map_step` の blind advance（A で場面送り）へ戻す。
  無条件 hold だと recall が 'map' を所有し続けて場面が最大 600 tick
  進まなくなる退行を防ぐ。
- 効果: 戦闘後のマップ復帰アニメーション・フェード等でカーソル
  スプライトが読めない区間に step 予算・`camp_missing_reads` 予算の
  双方を燃やさなくなる。

### 2. 戦闘割込み時の `to_camp`/`menu` steps リセット（案2）

- `_mark_recall_battle(mem)`（新規）: 戦闘系 kind と `mem['battle']`
  前景の2箇所で呼び、`stage in ('to_camp', 'menu')` のときだけ
  `state['battle_return'] = True` を立てる。`recall_interrupted`
  （`await_dispatch` かつ未送信専用の既存マーカー）とは分離した
  state 内フラグで、#3544 の stale marker 問題に触れない。
  text hold 経路では立てない（text 画面はカーソルを再配置しない）。
- `state.pop('battle_return')` を `pop('hold_since')` 直後で消費し、
  'map' フレームかつ `to_camp`/`menu` なら `_battle_return_reset()` で
  `steps` を 0 に戻す（このフレームは新しい基準の 1 観測目として
  `+1` される）。
- `_battle_return_reset(mem, state)`（新規）: 有界リセット。
  `battle_resets` が `RECALL_BATTLE_RESET_LIMIT`（3）以上、または
  初回リセット時刻 `battle_reset_since` から
  `RECALL_BATTLE_RESET_TICKS`（1800 tick）を超えた場合は何もしない。
  リセット発火時は `camp_recall_battle_reset` を `_record` する
  （`observed_metric`: stage / steps_spent / resets）。
- 根拠: 戦闘からマップへ戻るとフィールドカーソルは戦闘位置付近へ
  戻るため、戦闘前に積んだ steps は存在しない基準位置の債務であり、
  ナビはテントまでの徒歩を最初からやり直す。回数・総 tick の二重上限で、
  8 戦闘/月の割込みでも帰還が無制限に引き延ばされない。

### 3. 回帰テスト（案4）

`tests/test_hanjuku_recall_hold.py` に 6 件追加:

- `test_a_cursorless_map_frame_in_to_camp_holds_without_steps`:
  cursorless 'map'（HUD ヘッダつき）91 観測で `steps` 不変・
  `hold_since` 打刻・abort なし。実際に燃えた経路を再現する
  （既存 `test_a_battle_foreground_...` の 'battle' kind 版は v1373 でも
  加算されず本件を再現できていなかった）。
- `test_the_cursorless_map_hold_aborts_only_after_the_tick_limit`:
  600 tick 未満では保持、到達で `camp_recall_aborted`（`held_ticks`）。
- `test_a_hudless_map_is_not_held_as_a_fade`: HUD-less blind map は
  hold せず従来の加算経路（`camp_missing_reads` 経由の早期解放を維持）。
- `test_a_battle_return_restarts_the_to_camp_budget`:
  戦闘 → カーソルつき map 復帰で `steps` が 0 リセット→1 として
  カウントされ、ナビ入力が出る。
- `test_a_battle_return_restarts_the_menu_budget`: `menu` でも同様に
  リセット。
- `test_battle_resets_do_not_stretch_a_recall_forever` /
  `test_battle_resets_expire_outside_the_tick_window`:
  回数・tick 上限を超えたリセットは発火せず従来どおり 91 で abort。

### 4. バージョン

`src/docich/hanjuku/version.py`: `hanjuku-chart-v1397-recall-map-hold`。

## #5133 / #5129 系との競合確認（要件事項5）

- #5133 の hold 機構（`hold_since`・`_recall_hold_or_abort`）をそのまま
  再利用しており、新しい hold は同じ打刻・解除・abort 経路に乗る。
  `await_dispatch` 免除の仕様も変えていない（新 hold は `to_camp` 限定）。
- `recall_interrupted`（`await_dispatch` 未送信専用）には触れていない。
  `to_camp`/`menu` の戦闘割込みは別キー `battle_return` に記録するため、
  #3544 が解決した stale marker の再発要因を作らない。
- #5129（#5127、`arrival_unconfirmed` への名簿読み予約）は
  `_recall_dispatch_step` 内の処理であり無変更。
- steps 会計: cursorless 'map' は `steps += 1` へ到達する前に return、
  battle return リセットは加算前に `steps = 0` にするのみ。ステージ
  遷移時の `steps = 0`（`menu`/`y_route`/`hero_open`）や
  `await_dispatch` 内の独自加算は不変。負値化・二重加算の経路なし。

## 検証

- **pytest 未実施**: 本実行環境では python/pytest の実行がツール承認層で
  拒否される（VERIFY-5134 と同じ制約）。ローカル実行は関連ファイル
  （`tests/test_hanjuku_recall_hold.py` と recall/camp 系）のみが
  原則で、フルスイートは CI 想定。変更は静的レビューで確認済み:
  - cursorless 'map' hold は HUD 非持ち手作り Screen（`header=None`・
    `text=''`）では発火しないため、cursorless map を使う既存テスト
    （blind advance・camp_receipts・4468 等）は不変。
  - `battle_return` は `to_camp`/`menu` でしか立たず、`dest` の
    `camp_recall_battle` 記録テスト等も不変。
- 本番 SSH・VM 反映なし。push・PR なし。

## 残リスク

- VM の `hanjuku_decisions.jsonl` 未取得のままのため、'map' 燃焼の支配比
  （cursorless stall vs カーソルリセット非収束）は両対策を入れたが
  未確定。次回同シグネチャ再発時に `camp_recall_battle_reset` /
  hold 由来の `camp_recall_aborted`（`held_ticks`）の有無で切り分け可能。
- 戦闘後フェードが HUD も描画しない場合は blind 扱いとなり旧経路
  （加算・missing_reads）を取る。実機で支配的なら後続対応が要るが、
  現行シグネチャ（91 steps・'map'）は cursorless hold で解消する想定。
- 案3（nudge 許可）未実施のため、カーソル未検出が長期化する場合は
  600 tick の hold abort で帰還を諦める挙動は維持。
