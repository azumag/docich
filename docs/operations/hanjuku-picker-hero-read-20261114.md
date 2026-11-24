# 半熟英雄: goal が決まらないとき首都の在城一覧を読む（v828、2026-11-14）

## 症状（実測 g1163 2026-11-14 08:59/09:01）

帰還先ピッカーの `goal` が null のまま 90 observations の予算を使っていました。
チャプター9では月次パスが無効で `general_statuses` が null のため、v826 の
「主人公ステータスの位置」も使えません。主人公の城は在城一覧
（`world.garrison`）からも分からず、goal を決める材料がありませんでした。

## 修正（v828 `hanjuku-chart-v828-picker-hero-read`）

ピッカーの `goal` が None のとき、首都（`chart.home_castle`）の在城一覧を
180 tick 後に読む `roster_read_due` を登録します（まだ登録が無い場合のみ）。
corridor の due probe が首都の一覧を読み、次回のピッカーで主人公の城が
分かる可能性があります。挙動（ピッカーの操作）は不変更。

## 検証

- 新規 `test_a_goal_less_picker_schedules_the_capital_roster_read`:
  goal null で `roster_read_due[首都]` が設定される。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- goal null の局面で首都の一覧が読まれ、`garrison` に主人公が現れること。
- 次回のピッカーで `hero` が入り、goal が決まること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
