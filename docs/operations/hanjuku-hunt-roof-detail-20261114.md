# 半熟英雄: 野営探索の記録に屋根のセルを残す（v818、2026-11-14）

## 症状（実測 g1163 2026-11-14 06:57）

`camp_hunt` は `roofs: 2` のフレームでも `origin` が入りませんでした。
`observed_cursor_cell` の2個一致判定が失敗していることは分かりますが、屋根の
種類・画面セルが記録されておらず、どの城の対応が取れていないのかを特定
できませんでした。

## 修正（v818 `hanjuku-chart-v818-hunt-roof-detail`）

`camp_hunt` の `observed_metric` に `roofs_detail`（検出した屋根の
`kind` と `target` の一覧）を追加します。挙動は不変更。

## 検証

- 新規 `test_a_hunt_sample_records_the_roof_cells`:
  屋根2個のフレームで kind/target が記録される。
- 野営・帰還・月場面・field recall 関連の既存テストを実行。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 屋根2個で `origin` が入らない原因（城の対応・曖昧さ）を `roofs_detail` から
  特定できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
