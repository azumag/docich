# 半熟英雄: 差し替わった計画の未発射手順を破棄する（v594、2026-11-09）

## 指摘（オーナー）

「出撃済みの将軍におなじ出撃計画がたってるね」

## 原因

計画（`chart_plan`）が新しい `request_id` で差し替わっても、**旧計画の未発射（pending）の手順が
`mem['orders']` に残っていました**。実測（22:40 時点）でも、同じ将軍の手順が
`A:046e819d:M5f4f03`（旧計画・pending）と `A:8e54a3af:M5f4f03`（新計画・launched）の
2つに分かれて残っていました。

そのため配信の出撃計画には、**すでに出撃した将軍の計画が pending として**表示されていました。

## 修正（v594 `hanjuku-chart-v594-plan-superseded`）

計画を採用するときに `_supersede_unstarted_steps(mem, new_orders)` を呼びます。

- 旧計画の手順のうち、**新計画に含まれない**・**状態が pending/未設定**・**自分の sortie を
  持たない**ものを `superseded` にします（`plan_steps_superseded` を記録）。
- 実行中（launched など）の手順はそのまま。自分の sortie がある手順も残します。
- 新計画に同じ step が引き継がれている場合は何もしません。

## 確認

- `tests/test_hanjuku_plan_superseded.py`（新規3件）: 未発射は破棄／sortie 持ちは残す／
  新計画に引き継がれた手順は残す。
- 計画・チャート・経路・増援 469件 passed。
- 本番反映後: 出撃計画に**出撃済み将軍の pending 手順が出ない**ことを実測する。
