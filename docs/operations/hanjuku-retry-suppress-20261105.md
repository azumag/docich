# 直前に失敗した注文を別IDで即再発行しない（v415、2026-11-05）

## 症状（実測）

75分間で `order_start` / `order_launched` / `attack_observed` が **0件**。
その一方で `order_source_changed` が10分で **96件**、すべて同じボス指示
`A:bf8c3dee:M256898`・同じ `source: オケアナ`・`menu_miss: 3`。

- 城メニューが開かないため出撃元を本城へ切り替え、それも開かず注文は failed。
- しかし計画は状況ダイジェストが変わるたびに再構築され、**同じ内容の注文に新しい実行ID**
  （`A:<rid>:M256898`）が振られるため、失敗が「新しい注文」に見えて即再試行。
- 1回の再試行ごとに城までの移動とAをやり直していた。

## 変更

- `orders._finish_order(..., 'failed')` で、実行IDから内容名（`M256898`）を取り出し
  `mem['failed_local_step'][内容名] = tick` を記録。
- `replan._mechanical_chart` で新しい計画を採用する前に `_suppress_recent_failures` を呼び、
  直近 `PLAN_STALE_TICKS`（600観測・約15分）以内に失敗した内容名の注文は `failed` のままにする
  （`retry_suppressed` を計数）。
- 時間が経てば従来どおり再挑戦できる。

## 検証

- `tests/test_hanjuku_retry_suppress.py`（新規4件）: 直近の失敗は抑止／古い失敗は抑止しない／
  別の注文は無関係／失敗時に内容名を記録。修正前は4件とも失敗、修正後 4 passed。
- 関連（`chart_adjust` / `boss_sortie_overdue` / 層 / `off_chart`）210 passed。

## 未確認・残件

- 実機でボス指示の再試行連鎖が止まり、次の一手（別城からの出撃や別ターゲット）へ進むかは未確認。
- 城メニューが開かない原因そのもの（`menu_miss`）は未特定。
- 第6話ボス城への入城は依然として未実測。
