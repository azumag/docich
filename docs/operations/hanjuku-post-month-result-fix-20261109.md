# 半熟英雄: 月次巡回の診断フィールドを実際の記録に合わせる（v554、2026-11-09）

## 症状

v550（PR #3787）で `post_month_status_last.result` を診断へ投影しましたが、実機では
**常に `null`** でした。policy の `post_month_status_last` に `result` キーが存在しないためです
（実際のキー: `carry` / `complete` / `failed` / `manifest` / `manifest_done` /
`manifest_failed` / `scope` / `statuses` / `tick`）。

## 修正（v554 `hanjuku-chart-v554-post-month-result-fix`）

`_compact_post_month` を実際の記録に合わせて作り直しました。

- `complete`: 巡回完了（bool）
- `failed`: 失敗トークン（文字列のときだけ。自由文は出さない）
- `manifest_done`: 主人公の城の在籍名簿の確認が完了したか
- `manifest_arrived`: 名簿で到着が確認された人数
- `carry`: 章またぎ待ちの人数

存在しない `result` は削除しました。

## 確認

- `ops/vm_actions/tests/test_collect_diagnostics.py` の該当テストを新しい形へ更新。
- 本番反映後: 診断の `post_month` に `manifest_done` と `manifest_arrived` が入ることを
  実測する（次回の月替わり）。
