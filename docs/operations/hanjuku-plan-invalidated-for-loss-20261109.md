# 半熟英雄: 城を奪われたら暫定計画を捨てて奪還を先にする（v574、2026-11-09）

## 症状（実測）

g1163 第7話 2026-11-09 17:52 に **マンシー** が失陥。しかし暫定（mechanical）計画は
「ヘスティアをリデンブロックへ移動」の1件だけで、**奪還の指示がありませんでした**。

計画は「実行できる指示が無くなったとき」にだけ作り直されるため、移動が終わるまで
奪還候補（`_interim_source` の retake）が採用されませんでした。

## 修正（v574 `hanjuku-chart-v574-plan-invalidated-for-loss`）

`replan.invalidate_plan_for_loss(mem)` を追加し、`next_order` の先頭で呼びます。

- `lost` に城があり、暫定計画（`source == 'mechanical'`）が**その城の奪還指示を含まない**、
  かつ**実行中（launched / launched_unconfirmed）の指示が無い**なら、計画を破棄
  （`chart_plan = None`）し、`plan_invalidated_for_loss` を記録します。
- 次の判断で `_off_chart` が走り、奪還が先頭の候補として計画されます。
- 実行中の行軍があるときは計画を維持（進行中の指示を壊さない）。
- 既に奪還指示を含む計画、`lost` が無いとき、mechanical 以外の計画は従来どおり。

## 確認

- `tests/test_hanjuku_plan_invalidated_for_loss.py`（新規4件）: 破棄する／奪還入りは維持／
  実行中は維持／失陥なしは不変。
- 計画・チャート・行軍系 425件 passed。
- 本番反映後: 失陥時に `plan_invalidated_for_loss` が出て、次の計画が奪還を先に含むことを
  実測する。
