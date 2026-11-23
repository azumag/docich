# Issue #5154 — 砦帰還の診断証拠

## Scope

この変更は実フレーム校正前の診断強化のみ。砦帰還の入力、stage遷移、global `RECALL_LIMIT`、retry backoffは変更しない。未知画面や別将軍のstatus panelから確定入力を送らない。

## 追加する証拠

既存の `camp_recall_aborted` が `fort_menu` / `fort_list` / `fort_status` で発生した場合、次を記録する。

- 各stageの観測回数（各カウンターは `RECALL_LIMIT + 1` で上限）。
- `fort_status_reads`: 選択対象将軍と一致したstatus panelの読取回数。
- 期待将軍名と、最後にstatus panelから読めた将軍名。
- 最後のscreen kind。
- 連続空白・改行を正規化し、最大120文字に制限した最後の画面文言。

`month_audit` のincident projectionと生成Issue本文にも同じallowlisted fieldsを出す。OCR全文やraw frameは保存しない。

## Hot-load marker

`hanjuku-chart-v1404-fort-recall-evidence`。diagnostics allowlistと固定値テストを同期する。

## 回帰テスト

- 3つの砦stageでglobal limit abort時にstage別観測数とscreen kindを記録する。
- fort_statusで期待将軍と観測将軍を分離し、不一致を読取成功に数えない。
- 月次監査projectionとIssue本文で上限付きの証拠を保持する。

## 未完了の受入

次回の砦帰還失敗で実フレーム由来の `screen_kind` / `screen_text` / `observed_general` を取得し、`fort_status` または `fort_list` の停滞箇所を特定する。その証拠なしに再遷移・待機動作を推測で変更しない。