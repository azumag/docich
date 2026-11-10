# 半熟英雄: 月次ステータス巡回の結果を診断へ投影する（v550、2026-11-09）

## 背景

v547/v548 で月次巡回の重複読みを取り除き、実測では巡回が **4分04秒 → 1分44秒** に
短縮しました（09:58の巡回と11:15の巡回を比較）。しかし、その短縮が運用診断から
確認できる指標がありませんでした（`post_month_status_last` は policy にあるが未投影）。

## 変更（v550 `hanjuku-chart-v550-post-month-diagnostics`）

`collect_diagnostics._compact_post_month(last)` を追加し、compact policy に
`post_month` を追加します。

- `complete`: 巡回が完了したか（bool）
- `result`: 既知の結果トークン（`unchanged_cached` / `complete` / `reused` / `finished`）以外は `None`
- `carry`: 章またぎ待ちの人数（list の長さのみ）
- 未設定は `None`。将軍名・自由文は出しません。

## 確認

- `ops/vm_actions/tests/test_collect_diagnostics.py` に1件追加: 正常投影・未設定 None・
  未知トークンは None・自由文が出ないこと。
- 本番反映後: 毎月の `post_month.result` と `carry` を診断で追跡し、巡回時間の短縮を
  `complete` の出現タイミングで確認する。
