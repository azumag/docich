# 出撃元の切り替えを計画の再構築でも保持する（v416、2026-11-05）

## 症状（実測）

ボス指示 `A:bf8c3dee:M256898` で、10分間に `order_source_changed` が **96件**、
`y_jump_open` も同数。内訳はすべて `source: オケアナ`・`menu_miss: 3`。

- オケアナで城メニューが開かないため `_give_up_source` が本城（アルマムーン）へ切り替える。
- しかし計画は状況ダイジェストが変わるたびに再構築され、同じ内容の注文に**新しい実行ID**
  （`A:<rid>:M256898`）が振られる。
- `source_override` は実行IDで引くため、新しいIDでは一致せず、またオケアナを狙って同じ失敗を繰り返す。

## 変更

- `orders._source`: `source_override` を実行IDで引き、無ければ**内容名**（`M256898`）でも引く。
- `_give_up_source`: 本城への切り替えを実行IDと内容名の両方に記録する。
- これで再構築後も本城から出撃し直し、そこでも開かなければ注文は failed になり、
  v415 の再試行抑止が効く。

## 検証

- `tests/test_hanjuku_source_content_key.py`（新規3件）: 実行IDの上書きが優先／
  内容名の上書きが新しい実行IDにも効く／上書きが無ければチャートの出撃元のまま。
- 修正前は1件失敗、修正後 3 passed。関連（`chart_bot` / 層 / `retry_suppress` / `stale_active`）345 passed。

## 未確認・残件

- 実機で `order_source_changed` の連鎖が止まるかは未確認。
- 城メニューが開かない原因（`menu_miss`）そのものは未特定。
- 第6話ボス城への入城は依然として未実測。
