# 計画が長時間未発射のまま固まらないようにする（v406、2026-11-05）

## 背景

v405 で「移動の `purpose` が落ちて守備ルールに止められる」原因を直したが、
その修正は**計画が再構築されたとき**に初めて効く。

g1133 2026-11-05 の実測では、計画の再構築は「状況ダイジェスト（金・月・注文状態）が
変わったとき」だけだった。開始できない注文はどれも変えないため、
同じ計画が1時間以上 `pending` のまま残り、`order_start` が17分間ゼロだった。

## 変更（v406）

- `_adopt_plan` で `mem['chart_plan_tick']` に採用時 tick を記録。
- `_off_chart_mechanical` の冒頭で `_drop_stale_plan(mem)` を呼ぶ。
  計画の全注文が `None`/`pending` のまま `PLAN_STALE_TICKS`（600観測・約15分）を
  超えていたら計画を捨て、`plan_stale_rebuild` を記録する。
  次の `_mechanical_chart` が現在の状況から作り直す。
- 1件でも `launched` などの注文があれば捨てない（行軍中の計画を壊さない）。
  採用 tick がない古い計画も対象外（フェイルセーフ）。

## 検証

- `tests/test_hanjuku_plan_stale.py`（新規4件）: 長時間 pending で捨てる／
  まだ新しい計画は残す／開始済み注文がある計画は残す／採用 tick がない計画は残す。
- 修正前は4件とも失敗、修正後 4 passed。関連（`plan_purpose` / `chart_adjust` /
  `off_chart` / `mechanical_conquest` / 層 / `chart_bot`）を含め 517 passed。

## 未確認・残件

- 実機で `plan_stale_rebuild` が発火し、止まっていた移動が開始されるかは未確認。
- 第6話ボス城への入城は依然として未実測。
- 月次名簿スキャンが同じ月に2回走る（1回目は完走、2回目は `generals: []` で未完了）現象を
  40分で6組観測。次ラウンドで原因を調べる。
