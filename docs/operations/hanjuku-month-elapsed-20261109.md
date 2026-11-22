# 半熟英雄: 月次処理の所要時間を記録する（v579、2026-11-09）

## 背景

v578 で月次の将軍確認を短縮しましたが、**速くなったかを比較する記録がありませんでした**。
`post_month_sequence_finished` には完了・失敗・保留の内訳しかなく、所要時間が残りません。

実測例（v576 時点）: 18:19:30 の月は将軍確認に約272秒、全体では約7分半。

## 変更（v579 `hanjuku-chart-v579-month-elapsed`）

`post_month_sequence_finished` の `observed_metric` に **`elapsed_ticks`**
（`state['started_tick']` から現在 tick までの差）を追加しました。

- 既存フィールドは変更なし。
- 次回以降の月で「短縮後の所要」を数値で比較できます。

## 確認

- `tests/test_hanjuku_post_month_reuse_clean.py` に1件追加（記録に `elapsed_ticks` がある）。
- 月次関連 89件 passed。
- 本番反映後: 次の月替わりで `post_month_sequence_finished(elapsed_ticks=...)` を実測し、
  v578 の効果を数値で確認する。
