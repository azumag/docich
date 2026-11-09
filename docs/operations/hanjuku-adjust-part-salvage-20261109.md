# 半熟英雄: 調整チャートの任意部分が壊れても注文は捨てない（v535、2026-11-09）

## 症状

`hanjuku_chart_history.jsonl` の `adjust_worker` 集計（g1163、本セッション）:

| 結果 | 件数 |
|---|---|
| saved | 18 |
| superseded | 7 |
| generate_failed rc-1:rate_limit | 7 |
| **generate_failed invalid_output 'invalid purchases'** | **5** |
| generate_failed rc-124:timeout | 2 |

`invalid purchases` は、LLM の回答のうち `purchases`（月の購入計画）だけが
検証を通らず、**注文（orders）は有効なのに回答全体を破棄**していました。
1件ごとに LLM の再生成と待ち時間（1〜3分）が発生します。

## 原因

`hanjuku_chart_adjust.validate()` が任意部分の正規化失敗を `ValueError` として投げ、
`save()` が回答全体を拒否していました。今回の観測は `purchases`（月の購入計画）だけで、
`recruitment` は採用人数を左右するため従来どおり厳格に検証します。

## 修正（v535 `hanjuku-chart-v535-adjust-part-salvage`）

- `validate()`: `purchases` の正規化だけを try し、失敗したら `None` にして
  `dropped_parts` に記録する。**注文（orders）と `recruitment` は従来どおり厳格に検証**し、
  不正なら回答全体を拒否する。
- `save()`: `dropped_parts` があるとき `adjusted_parts_dropped` を履歴へ残す。
- 計画は注文だけで成立するため、購入・募集の欠落は次回の調整で再計画できる。

## 確認

- `tests/test_hanjuku_chart_adjust.py`:
  購入部分が不正でも注文は採用され `purchases=None` / `dropped_parts=['purchases']`、
  `save` が履歴へ `adjusted_parts_dropped` → `adjusted_chart_saved` を残す。
  注文そのものの不正は従来どおり拒否。
- チャート調整・計画・攻撃波 536件 passed。
- 本番反映後: `adjusted_parts_dropped` が出た回で `adjusted_chart_saved` が続き、
  `invalid_output 'invalid purchases'` が減ることを実測する。
