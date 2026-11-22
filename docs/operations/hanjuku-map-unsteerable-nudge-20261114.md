# 半熟英雄: カーソルの無い地図で150秒待たずに SELECT/A で動かす（v834、2026-11-14）

## 症状（実測 g1153 2026-11-07 22:53-22:55、Issue #3721）

章送りの直後の次章地図が **HUD・文字・カーソルなし**のまま 174 秒続き、入力を
1回も送れませんでした。フレームは同一ハッシュ（`frame-076`〜`082`）。手動で A を
送ると会話が進み、月メニューが出ています。22:53:03–22:55:53 は
`screen_kind=map` かつ `planned_actions=[]` でした。

## 原因

- `map_step` は位置を屋根で再特定できない地図では入力を保留します。
- カーソルの無い地図は `nav_step` の SELECT 復帰（`SELECT_AFTER`）にも入りません
  （`mem['uncertain']` の経路は `world` と画面カーソルがあるときだけ）。
- そのため一般の無入力ゲート（`NO_INPUT_HOLD_MAX=100`、約150秒）まで待って
  ようやく既定入力が出ます。章送りの場面では、その間に画面が進みません。

## 修正（v834 `hanjuku-chart-v834-map-unsteerable-nudge`）

`orchestrator.map_unsteerable_nudge()` を追加し、無入力ゲートの手前で判定します。

- 条件: `phase=='field'`、`kind=='map'`、カーソル・マーカー・文字がすべて無い、
  同一フレーム（同じ digest）で `MAP_UNSTEERABLE_HOLD_MAX=8` 観測を超えて無入力。
- 1回目の固着: **SELECT を1回**。カメラを主人公の城へ戻す実測済みの復帰
  （`select_to_hero` と同じ意味）で、`nav_last` を捨て `uncertain` を立てます。
- 2回目（SELECT でも画面が変わらない）: **A を1回**。ただし計画に注文が残って
  いる間は送りません（見えないカーソルの位置でメニューを開くのを避けるため）。
- 同じ digest の間に SELECT と A は各1回まで。それでも動かない画面は従来の
  無入力ゲートに任せます（挙動を悪化させない）。
- 地図の操作保留は約150秒から約12秒（8観測×1.5秒）＋復帰入力に短縮されます。

## 回帰テスト

`tests/test_hanjuku_map_unsteerable.py`（新規6件）:
- 固着した裸の地図が SELECT を1回だけ送り、`uncertain` と `select_focus_tick` を残す
- 2回目は A を1回だけ送り、3回目は何も送らない
- 計画に注文がある間は A を送らない
- フレームが変われば新しい固着として SELECT からやり直す
- 境界（8観測以下、transition、kind違い、カーソルあり、文字あり）では送らない
- `decide()` 経由で、無入力ゲート（100観測）より前に SELECT が出ること

## 検証

- 新規6件と既存の無入力・保留系（`test_hanjuku_no_input_gate` / `text_hold_advance` /
  `unknown_hold` / `semantic_hold_gate` / `cursor_still`）27件 passed。
- 最終判定は CI（hanjuku-suite）と DSH の自己レビュー。独立レビューは未取得。

## 本番反映後に実測すること

- 章送り直後の地図で `map_unsteerable_nudge`（`steps:1` の SELECT）が記録され、
  `planned_actions=[]` の連続が8観測（約12秒）以内に終わること。
- `no_input_streak` が100に達する事例が、この形の地図で消えること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
