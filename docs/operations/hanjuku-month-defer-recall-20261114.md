# 半熟英雄: 月の終わり際には野営帰還を始めない（v802、2026-11-14）

## 症状（実測 g1163 2026-11-14 01:35）

野営のメニューを開き「きかん」まで到達したあと、帰還先の選択（dest）が
42-8 の月初場面で中断しました（`steps 1`）。月の変わり目の場面は、
野営帰還の途中でも問答無用で picker を閉じます。

```
01:34:33 camp_found
01:35:14 きかん（here）
01:36:50 camp_recall_interrupted {"stage": "dest", "steps": 1}
```

帰還は約2分かかるため、月の終わり際に始めると場面に切られやすくなります。

## 修正（v802 `hanjuku-chart-v802-month-defer-recall`）

- 月の表示を観測したとき、`month_start_tick` と直近6件の
  `month_durations` を記録（`prompts.py`）。
- `recall._month_end_soon(mem, margin=150)` を追加。直近3件の最大長から
  推定した月の終わりまで `margin` tick を切っていたら True。
- `camp_recall_step` の**新規野営の選択**で、月の終わり際なら何もせず待つ
  （既存の「空のフィールドでは何もしない」ルールと同じく入力しない）。
  月が変わってから野営を拾い直します。

## 検証

- 新規 `test_a_recall_near_the_month_end_waits_for_the_scene`:
  月の残り150 tick 未満では `camp_recall_step` が None、余裕があれば
  野営を拾う。
- 野営・帰還・field recall・月場面関連の既存テストを実行。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 月の終わり際に `camp_found` が出ても、帰還を開始しないこと。
- 月が変わったあと、場面に切られずに dest まで進むこと。
- 帰還が成立し、野営の将軍が城に戻ること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
