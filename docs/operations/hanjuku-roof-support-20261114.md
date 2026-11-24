# 半熟英雄: 野営探索の記録に屋根の一致度を残す（v820、2026-11-14）

## 症状（実測 g1163 2026-11-14 07:32）

`camp_hunt` は `roofs: 2` で `roofs_detail` に
`[{"kind":"own","target":[147,140]}, {"kind":"enemy","target":[207,10]}]`
を記録しましたが、`origin` は入りませんでした。`observed_cursor_cell` の
2個一致判定が失敗した理由（屋根が足りないのか、星座が一致しないのか）を
切り分けられませんでした。

## 修正（v820 `hanjuku-chart-v820-roof-support`）

- `world.roof_camera_support(mem, frame)` を追加。2個以上のゲートを外して、
  屋根の星座が最も一致するカメラと支持数を返す。
- `camp_hunt` の `observed_metric` に `camera_support`（最大支持数）と
  `camera`（そのカメラ）を追加。挙動は不変更。

## 検証

- 新規 `test_a_hunt_sample_records_the_roof_support`:
  屋根2個で camera_support/camera が記録される。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `recall.py` / `world.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 屋根2個で origin が入らない局面の `camera_support` を確認し、星座の不一致
  か屋根不足かを切り分けられること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
