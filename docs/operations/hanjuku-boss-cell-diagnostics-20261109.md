# 半熟英雄: 実測ボスセルを診断へ投影する（v539、2026-11-09）

## 背景

v536/v538 で「Yビューのドクロから測ったボスセル」（`boss_cell`）を実行時状態として
追加しましたが、owner-only 診断の compact policy へ投影していませんでした。
AGENTS §11（runtime 変更時の diagnostics coverage）を満たすため、
固定トークンだけで公開します。

## 変更（v539 `hanjuku-chart-v539-boss-cell-diagnostics`）

- `collect_diagnostics._compact_boss_cell(reading, now)`: `boss_cell` から
  `chapter` / `cell` / `readings` / `age_ticks` だけを取り出す。
  - 型が違う値は `None`。`readings` は正の整数のみ。
  - `age_ticks` は現在 tick との差（負なら 0）。
  - 自由文・identity・フレームハッシュは出さない。
- compact policy へ `boss_cell` フィールドを追加（無ければ `None`）。

## 確認

- `ops/vm_actions/tests/test_collect_diagnostics.py` に2件追加:
  正常な実測は `{chapter, cell, readings, age_ticks}` に投影され自由文を含まない、
  不正値は `None` になり `PRIVATE` も出ない、未設定は `None`。
- 同ファイル 198件（boss_cell 2件を含む）passed。
  `CollectorContractTests` の2件は soren root/イベント fixture を必要とする環境依存の
  既存失敗で、今回の変更（vacabulary 追加のみ）とは無関係。
- 本番反映後: 診断の policy に `boss_cell` が出ることを実測する
  （第5話は実測対象外なので `null`、第6話以降で値が入る）。
