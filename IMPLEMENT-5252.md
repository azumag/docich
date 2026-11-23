# Issue #5252 — 砦帰還失敗の診断証拠

## 対象

Issue #5252（ch9 12-5、砦帰還が `fort_status` で91 stepsを消費して中断した事案）。関連する先行事象は #5154。

## 範囲

実フレーム校正前の診断強化のみ。帰還入力、stage遷移、global `RECALL_LIMIT`、retry backoffは変更しない。未知画面や別将軍のstatus panelから確定入力を送らない。

## 記録する証拠

- `fort_menu` / `fort_list` / `fort_status` の観測回数（各カウンターは `RECALL_LIMIT + 1` でbounded）。
- `fort_status_reads`: 選択対象将軍と一致したstatus panelの読取回数。
- 期待将軍と、最後にstatus panelから読めた将軍名を分離。
- 最後のscreen kindと、空白・改行を正規化して最大120文字に制限した画面文言。
- month audit projectionと生成Issue本文にも同じallowlisted fieldsを投影。OCR全文・raw frame・座標は追加しない。

## Hot-load marker

`hanjuku-chart-v1433-fort-recall-evidence`（当初 v1422、main 再同期時に v1433 へ再採番）。mainのv1421 critical-card-spendを保持したうえで、diagnostics allowlistと固定値テストを同期する。

## 回帰テスト

- 3つの砦stageでglobal recall limit abort時のstage別観測数・screen kindを検証。
- fort_statusで期待将軍と観測将軍を分離し、不一致を読取成功に数えない。
- month audit projectionとIssue本文にbounded evidenceが出ることを検証。

## 未完了の受入

次回の砦帰還失敗で実フレーム由来のscreen evidenceを取得し、停滞箇所を特定する。その証拠なしに再遷移・待機動作を推測で変更しない。Issue #5252は実機再観測までOpenのまま維持する。
