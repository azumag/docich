# 半熟英雄: 帰還先ピッカーの進捗に月内位置を残す（v830、2026-11-14）

## 症状（実測 g1163 2026-11-14 10:15-10:16）

帰還先ピッカーの進捗は observations / cursor / goal / nearby / own / hero を
記録していましたが、月のどのあたりで何回目かが分かりませんでした。実際に
34 observations まで進んだところで月替わりの場面に切られており、ピッカーの
停滞が「月の終わり際」によるものかを記録から判断できませんでした。

## 修正（v830 `hanjuku-chart-v830-picker-month-timing`）

`camp_recall_picker_progress` の `observed_metric` に `month_elapsed`
（月の開始からの経過 tick）と `month_estimate`（直近3件の最大）を追加します。
挙動は不変更。

## 検証

- 新規 `test_the_picker_progress_records_the_month_timing`:
  month_elapsed=100 / month_estimate=464 が記録される。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- ピッカーの停滞が月の終わり際（遷移前）で起きているかを
  `month_elapsed` / `month_estimate` で確認できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
