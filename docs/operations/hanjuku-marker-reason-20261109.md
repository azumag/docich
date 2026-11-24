# 半熟英雄: 選択マーカーが結び付かない理由を記録する（v596、2026-11-09）

## 背景（実測）

直近40分で `post_month_roster_marker_unverified` が **53件**、`mapped` が 11件。
未確認1件ごとに将軍のステータスパネルを開くため、月次処理の主要コストになっています。
しかしログには**理由が残っていません**でした（`icons_seen` も大半が None）。

## 変更（v596 `hanjuku-chart-v596-marker-reason`）

`readers.marker_castle` に `evidence` 引数を追加し、結び付かなかった理由を残します。

- `no_frame` / `no_marker` / `out_of_tolerance` / `ambiguous` / `mapped`
- `post_month_status._marker` が `marker_reason` として結果に載せ、
  `post_month_roster_marker_unverified` / `_mapped` の `observed_metric` に記録。

## 確認

- `tests/test_hanjuku_marker_hint.py` に1件追加（マーカーが無いとき `no_marker`）。
- 月次・マーカー系 88件 passed。
- 本番反映後: 未確認の内訳（曖昧／許容範囲外／マーカー無し）を実測し、
  最多の原因に対する修正を次に入れる。
