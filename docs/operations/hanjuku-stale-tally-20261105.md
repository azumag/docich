# 計画の再構築を記録ではなく回数で数える（v412、2026-11-05）

## 症状（実測）

v409（state 署名）と v411（プロセス内署名）の両方を入れても、
`plan_stale_rebuild` が止まらなかった。

- v411 稼働1.2分で **35件**、すべて同一内容（`age: 2045`、同じ3指示）。
- live policy の `plan_stale_signature` は常に None、`chart_plan_tick` も更新されない。

## 原因

計画を組み立てる経路の一部（chart worker）が **mem のコピー**で動き、
さらにホットリロードでモジュール状態も毎回失われるため、
state 署名もプロセス内署名も効かなかった。記録だけが同じログへ出続けていた。

## 変更

- `plan_stale_rebuild` の**決定ログ記録をやめ**、有界カウンタ `_tally` に置き換えた。
  計画を捨てる挙動自体は変えない（`mem['chart_plan'] = None`）。
- 署名ガード（state / プロセス）は不要になったので撤去した。

## 検証

- `tests/test_hanjuku_plan_stale.py` を新仕様に更新（5件）: 古い計画は捨てる／新しい計画は残す／
  開始済み注文がある計画は残す／採用 tick がない計画は残す／**決定ログには書かない**。
- 関連（`chart_adjust` / 層 / `chart_bot` / `off_chart` / `mechanical_conquest`）513 passed。

## 未確認・残件

- 実機で `plan_stale_rebuild` のログが消えるかは未確認（次ラウンドで確認）。
- chart worker が状態を捨てる設計そのものは未整理。
- 第6話ボス城への入城は依然として未実測。
