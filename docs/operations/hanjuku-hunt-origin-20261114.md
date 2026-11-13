# 半熟英雄: 野営探索の記録にカメラ測位の可否を残す（v816、2026-11-14）

## 症状（実測 g1163 2026-11-14 06:05/06:12）

野営探索の `camp_hunt` は `roofs: 0` / `roofs: 1` でしたが、そのフレームで
カメラ位置（`observed_cursor_cell`）が測れたかどうかは記録されていません
でした。v795 の `camp_world` が設定されない原因が「屋根が足りない」のか
「屋根はあるが測位に失敗」なのかを切り分けられませんでした。

## 修正（v816 `hanjuku-chart-v816-hunt-origin`）

`camp_hunt` の `observed_metric` に `origin`（`observed_cursor_cell` が
測位できたときのワールドセル）を追加します。測位できなければ入りません。
挙動は不変更（入力もしない）。

なお、屋根1個でも城主の種別一致で測位する案は試しましたが、既存の
`test_origin_is_observed_from_current_roof_constellation`（2個以上ルール）に
反したため**取り下げ**ました。

## 検証

- 新規 `test_a_hunt_sample_records_whether_the_camera_was_localised`:
  測位できたとき origin が記録される。
- 野営・帰還・月場面・field recall 関連の既存テストを実行。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- `camp_hunt` の roofs と origin の有無で、測位失敗の原因を切り分けられること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
