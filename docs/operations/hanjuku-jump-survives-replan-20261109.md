# 半熟英雄: 計画から旧ステップが消えても出撃元への移動を続ける（v617、2026-11-10）

## 症状（実測 06:22）

v616 後も `castle_jump_failed(why=order_changed, phase=menu, age=1)` が続きました
（25分で7回）。直後の並びは `chart_interim_hold → order_start → castle_jump_open →
castle_jump_failed(order_changed)`。

## 原因

計画の差し替えで**旧ステップ自体が消える**と `_order_for_step(mem, jump['step'])` が
`None` を返し、v615/v616 の引き継ぎ判定がそこで打ち切られていました。

## 修正（v617 `hanjuku-chart-v617-jump-survives-replan`）

- ジャンプ開始時に **出撃先(target)** も記録。
- 旧ステップが消えていても、**記録した出撃元・出撃先が現在のアクティブ指示と一致**すれば
  ジャンプを引き継ぎます（将軍が変わっていても可）。

## 確認

- `tests/test_hanjuku_castle_jump.py` に1件追加（旧ステップ消失時も継続／目標が違えば中断）。
- ジャンプ・チャート 129件 passed。
- 本番反映後: `castle_jump_failed(order_changed)` が減り、`order_launched` が増えることを実測する。
