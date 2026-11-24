# 半熟英雄: 計画の差し替えで出撃元への移動を中断しない（v615、2026-11-10）

## 症状（実測 05:38〜05:50）

`castle_jump_failed` が **`why: order_changed`, `phase: menu`, `age: 1`** で5回。
出撃元の城へ飛ぶ動作が**1観測目で中断**され、そのまま
`close_panel → cursor_still_search → select_to_hero → order_start …` を繰り返して、
**出撃が成立しない**状態になっていました。

## 原因

チャートworkerが計画を差し替えると**全ステップのIDが振り直され**、
`mem['active'] != jump['step']` となって「注文が変わった」と判定されていました。
しかし**同じ将軍が同じ城から同じ城へ向かう**なら、物理的な移動はそのまま有効です。

## 修正（v615 `hanjuku-chart-v615-jump-across-replan`）

`_jump_successor(mem, order)` を追加し、計画差し替え時も

- **出撃先(target)が同じ**
- **出撃元(source)が同じ**
- **将軍が同じ**（または同じ将軍の上書きが両方に記録されている）

なら、進行中のジャンプを新しいステップIDへ**引き継いで続行**します。
どれかが変わった場合だけ従来どおり中断します。

## 確認

- `tests/test_hanjuku_castle_jump.py` に1件追加（同一なら継続／別将軍・別目標なら中断）。
- ジャンプ・カーソル・出撃優先度・チャート 483件 passed。
- 本番反映後: `castle_jump_failed(order_changed)` が減り、`order_launched`／`attack_observed` が
  増えることを実測する。
