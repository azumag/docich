# 半熟英雄: 曖昧な選択マーカーを「記録済みの城」で確定する（v590、2026-11-09）

## 症状（実測・40分）

`post_month_roster_marker_unverified` が **29件**。1件ごとに将軍のステータスパネルを
開いて城を確認しており、月次処理の主要なコストになっています。

## 原因

`readers.marker_castle` は、選択マーカーの直下に**近い城が2つある**場合
（2番目が1.5倍以内）は「曖昧」として `None` を返していました。
その将軍が**直前にどの城にいたか**は記録済みなのに、使っていませんでした。

## 修正（v590 `hanjuku-chart-v590-marker-hint`）

- `marker_castle(frame, chapter, hint=None)` に **`hint`**（その将軍の記録済みの城）を追加。
- 曖昧なとき、`hint` の城がマーカーから `MAP_CASTLE_TOLERANCE`（96）以内なら
  **その城で確定**します。範囲外なら従来どおり `None`。
- `post_month_status._marker` が `_recorded_castle(mem, general)`（月次スナップショット →
  `general_statuses` → `house_eggs` の順）を hint として渡します。

## 確認

- `tests/test_hanjuku_marker_hint.py`（新規3件）: 曖昧なマーカーは hint 無しで `None`／
  hint があれば確定／範囲内なら hint が勝つ。
- 月次・マーカー系 88件 passed。
- 本番反映後: `post_month_roster_marker_unverified` が減り、月次処理が短くなることを
  実測する（`post_month_sequence_finished.elapsed_ticks`）。
