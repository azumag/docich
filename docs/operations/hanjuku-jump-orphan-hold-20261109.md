# 半熟英雄: 計画が一瞬空でも出撃元への移動を止めない（v618、2026-11-10）

## 症状（実測 06:40）

v617 後も `castle_jump_failed(why=order_changed, phase=menu, age=1)` が **25分で5回**。

## 原因

計画の合間（`chart_interim_hold` の直後など）に **`mem['active']` が一瞬空**になり、
その1フレームで「注文が変わった」と判定して移動を中断していました。

## 修正（v618 `hanjuku-chart-v618-jump-orphan-hold`）

`active` が空のときは**最大4フレーム（`CASTLE_JUMP_ORPHAN_HOLD`）待機**し、
その間に指示が戻ればジャンプを継続します。戻らなければ従来どおり中断。

## 確認

- `tests/test_hanjuku_castle_jump.py` に1件追加（空の active で待機）。
- ジャンプ・チャート・出撃優先度 138件 passed。
- 本番反映後: `castle_jump_failed(order_changed)` が消え、`order_launched` が増えることを実測する。
