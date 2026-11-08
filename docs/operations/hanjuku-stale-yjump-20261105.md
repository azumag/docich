# 計画にも有効注文にも属さない指示でYジャンプを始めない（v424、2026-11-05）

## 症状（実測）

v423 稼働2.5分で `y_jump_open` が **72件**、すべて同じ指示 `A:bf8c3dee:M97e81e`、
すべて `mode: map`・`attempt: 1`。

- その指示は現在の計画（`M256898` / `Ma21426` / `M22c018`）にも `active` にも含まれない。
- 計画から外れた指示のジャンプ記憶が残り続け、毎観測「Yビューを開く」を繰り返していた。

## 変更

- `os_layer/navigation._want_y_jump`: 計画が存在し、その指示が計画にも `active` にも無ければ
  Yジャンプを始めない（歩いて近づく）。
- `flows/map._drop_jump_state(mem, step)` を追加し、古い有効注文を解放するときに
  その指示のジャンプ記憶（`y_jumps` / `y_jumped` / `y_jump_last` / 進行中 `y_jump`）も消す。

## 検証

- 関連（`stale_active` / `boss_cell_probe` / `chart_bot` / 層 / `giveup_fallback` / `off_chart`）436 passed。

## 未確認・残件

- 実機で `y_jump_open` の連続が止まるかは未確認。
- 第6話ボス城への入城は依然として未実測。
