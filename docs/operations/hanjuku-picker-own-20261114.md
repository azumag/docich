# 半熟英雄: 帰還先ピッカーの進捗に自軍城を残す（v822、2026-11-14）

## 症状（実測 g1163 2026-11-14 08:05）

v819 の `camp_recall_picker_progress` で、ピッカーの `goal` が **null** のまま
10/20/30 observations と進み、`nearby` も空（または敵城のみ）であることが
分かりました。しかし、その時点でどの城が自軍なのか（`world_flags` の own）が
記録に無く、goal が null になる理由を切り分けられませんでした。

なお、`_recall_goal` の最後の手段を「首都」に変える案は試しましたが、既存の
帰還先テスト5件（`test_unconfirmed_hero_is_not_used_as_a_merge_target` など）
に反するため**取り下げ**ました。

## 修正（v822 `hanjuku-chart-v822-picker-own`）

`camp_recall_picker_progress` の `observed_metric` に `own`（world_flags で
own と読めた城の一覧）を追加します。挙動は不変更。

## 検証

- 新規 `test_the_picker_progress_records_the_own_castles`:
  進捗記録に自軍城の一覧が入る。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- ピッカーの goal null の局面で、`own` に自軍城が入っているかを確認し、
  `_recall_goal` が None を返す理由（origin/hero/departure/首都）を特定
  できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
