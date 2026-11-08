# 同じ計画を毎回採用し直さない（v413、2026-11-05）

## 症状（実測）

`chart_adjust_applied` が約40分で **490件**、うち **487件が同一内容**
（`request_id: bf8c3dee6398cea3`、同じ4つの step）。

- 状況（金・月・注文状態）は変わっていないのに、計画を作るたびに同じ候補列を作り、
  毎回 `_adopt_plan` していた。
- v400 の署名抑制は「同じ policy 状態」では効くが、この経路は別プロセスのコピーでも走るため、
  記録だけが積み上がっていた。

## 変更

- `_mechanical_chart`: 作り直した候補の step 列が、保持している計画の step 列と**同一**なら、
  計画を差し替えずにそのまま返す（`chart_plan` と `chart_plan_tick` は保持）。
- step 名は「source>target:purpose」の内容ハッシュなので、step 列の一致は計画内容の一致を意味する。
- 状況が変わって候補が変われば、従来どおり新しい計画を採用する。

## 検証

- `tests/test_hanjuku_plan_readopt.py`（新規3件）: 同じ状況では採用は1回だけ／
  保持した計画は同一オブジェクトで tick も変わらない／候補が変われば差し替える。
- 関連（`chart_adjust` / `decision_dedupe` / 層 / `chart_bot` / `mechanical_conquest` / `off_chart`）516 passed。

## 未確認・残件

- 実機で `chart_adjust_applied` の増加が止まるかは未確認。
- 別プロセスのコピーで計画を作る経路そのもの（chart worker）は未整理。
- 第6話ボス城への入城は依然として未実測。
