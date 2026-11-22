# 半熟英雄: 帰還先ピッカーのフォールバックでも帰還先を名付ける（v829、2026-11-14）

## 症状（実測 g1163 2026-11-14 10:12）

帰還先ピッカーの `goal` が null・`nearby` も空のまま 90 observations の予算を
使い切ると、`_picker_fallback` が「今いる城」を確定します。しかし近傍にも
目標にも城が無い場合、`_request_recall` に渡す帰還先が **None** のままになり、
`camp_recall_requested` の castle が空・`roster_read_due`（到着後の一覧読み）
も登録されませんでした。

## 修正（v829 `hanjuku-chart-v829-picker-fallback-home`）

`_picker_fallback` は、近傍の城も `goal` も無いとき、首都
（`chart.home_castle`）を帰還先として名付けます。挙動（確定操作）は不変更。

## 検証

- 新規 `test_the_picker_fallback_names_the_capital_when_no_goal`:
  goal null・nearby 空のフォールバックで recall の goal が首都になる。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- フォールバック時に `camp_recall_requested` の castle が入り、
  `roster_read_due` が登録されること。
- 帰還した将軍が首都の一覧で確認できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
