# 半熟英雄: 帰還先ピッカーの進捗に主人公ステータスの位置を残す（v827、2026-11-14）

## 症状

v826 で `_recall_goal` が月次ステータス（`general_statuses`）の主人公位置を
使えるようにしましたが、`camp_recall_picker_progress` の `hero` は在城一覧
（`world.garrison`）だけでした。チャプター9では月次パスが無効で
`general_statuses` が null のことが多く、goal null の局面で「ステータス側の
位置が有ったのか」を記録から確認できませんでした。

## 修正（v827 `hanjuku-chart-v827-picker-hero-status`）

`camp_recall_picker_progress` の `observed_metric` に `hero_status`
（主人公ステータスの `location` と `observed`）を追加します。挙動は不変更。

## 検証

- 新規 `test_the_picker_progress_records_the_hero_status_location`:
  ステータス位置が記録される。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- goal null の局面で `hero`（在城）と `hero_status`（ステータス）のどちらが
  欠けているかを確認できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
