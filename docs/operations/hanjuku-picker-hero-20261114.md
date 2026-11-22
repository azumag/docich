# 半熟英雄: 帰還先ピッカーの進捗に主人公の所在を残す（v825、2026-11-14）

## 症状（実測 g1163 2026-11-14 08:59）

v822 の `camp_recall_picker_progress` で `own` に自軍5城が入っているのに、
ピッカーの `goal` が **null** のままでした。`_recall_goal` が None を返す
理由のひとつが「主人公の所在が未確認」ですが、その時点で主人公の城が
分かっていたのかを記録していませんでした。

なお、goal null のときのピッカー予算を90→30に短縮する案は、既存の
`test_picker_stall_has_a_finite_budget`（90回の有限予算）に反するため
**取り下げ**ました。

## 修正（v825 `hanjuku-chart-v825-picker-hero`）

`camp_recall_picker_progress` の `observed_metric` に `hero`（主人公が
在城と読めている城の一覧）を追加します。挙動は不変更。

## 検証

- 新規 `test_the_picker_progress_records_the_hero_castle`:
  進捗記録に主人公の城が入る。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- goal null の局面で `hero` が空かどうかを確認し、`_recall_goal` が None を
  返す理由（主人公の所在未確認）を特定できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
