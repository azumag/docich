# 出撃元を諦める前の状態を記録し、本城が使えない場合は諦め切る（v419、2026-11-05）

## 症状（実測）

ボス指示 `A:bf8c3dee:M256898` で `order_source_changed` が453件、
`source_miss` は **5のまま**、`source_override` に M256898 は現れない。

- v418 の「諦めが効かないときは b を押す」判定は、`_give_up_source` が同じ観測内で
  `source_miss` を pop するため到達しなかった（実測: 1.2分で0件）。
- 状態が捨てられているのか、フォールバック先（本城）が出撃できないのかを切り分ける必要がある。

## 変更

- `_give_up_source` を呼ぶ直前に `source_giveup_probe` を記録: `source_miss` / `override` /
  現在の `source` / `home` / 本城の実測名簿。次回はこの記録で原因を確定できる。
- `_give_up_source`: フォールバック先の本城が `_last_castle_held`（最後の1人を出せない）に
  該当する場合は、本城へ切り替えずに注文を failed にする（出せない城へ切り替えて
  毎観測やり直すのを避ける）。

## 検証

- 既存の関連テスト（`boss_sortie_overdue` / `off_chart` / `castle_panel` / `giveup_fallback` /
  `source_content_key` / `source_mismatch`）129 passed。
- 追加の記録は診断用で、挙動の変更は「本城が出せないときは failed」のみ。

## 未確認・残件

- 実機で `source_giveup_probe` が何を示すか（state 破棄か本城の拒否か）は未確認。
- 第6話ボス城への入城は依然として未実測。
