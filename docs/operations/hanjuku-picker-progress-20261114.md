# 半熟英雄: 帰還先ピッカーの進捗を記録する（v819、2026-11-14）

## 症状（実測 g1163 2026-11-14 07:13-07:18）

野営の「きかん」のあと、帰還先の全島ピッカー（`world_map_step` の dest）が
**31 observations** のあいだ進まず、`steps 0` のまま約5分経過していました。
ピッカーのカーソル・目標・近傍の城が記録されていないため、なぜ確定できない
のかを特定できませんでした。

## 修正（v819 `hanjuku-chart-v819-picker-progress`）

ピッカーの観測が10回ごとに `camp_recall_picker_progress` を記録します
（挙動は不変更、入力もしない）。

- `observations`: ピッカーの観測回数
- `cursor`: ワールドカーソル（測位できなければ null）
- `goal`: 目標の城
- `nearby`: カーソル近傍の城

## 検証

- 新規 `test_the_picker_records_its_progress_every_ten_observations`:
  10回目の観測で進捗が記録される。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- ピッカーの cursor/goal/nearby から、31 observations で確定できない原因を
  特定できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
