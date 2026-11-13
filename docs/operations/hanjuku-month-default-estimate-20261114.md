# 半熟英雄: 再起動直後の月は保守的な既定長で場面保護する（v803、2026-11-14）

## 症状（実測 g1163 2026-11-14 02:0x）

v802 の反映後、`month_start_tick` は 151146（42-10 の開始）と記録されましたが、
`month_durations` は None のままでした。反映後最初の月替わりには「前の月の
開始tick」が無いため、月の長さが測れません。その結果、月の終わり際の場面保護
（`_month_end_soon`）がもう1か月ぶん効かず、野営の帰還先選択が再び切られる
可能性がありました。

## 修正（v803 `hanjuku-chart-v803-month-default-estimate`）

- `MONTH_DEFAULT_TICKS=320` を追加。
- 測定済みの `month_durations` が無いときは、この既定長を推定に使う
  （月の開始から約170 tick を過ぎたら新規野営の選択を待つ）。
- 測定値が入れば従来どおり直近3件の最大長を使う。

## 検証

- 新規 `test_the_first_month_after_a_restart_uses_a_conservative_default`:
  durations 空で、開始から100 tick は False、200 tick は True となり
  `camp_recall_step` が待つ。
- 野営・帰還・月場面・field recall 関連の既存テストを実行。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 42-10 で `month_durations` が空でも、開始から約170 tick 以降の
  `camp_found` が帰還を開始しないこと。
- 42-11 で最初の実測長が入り、以後は実測ベースの推定になること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
