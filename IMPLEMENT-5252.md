# Issue #5252 — 砦帰還失敗の診断証拠

## 対象

Issue #5252（ch9 12-5、砦帰還が `fort_status` で91 stepsを消費して中断した事案）。関連する先行事象は #5154。加えて、read-only検証で判明した #5248 の崖離脱Aと通常帰還Aの誤分類を同じ監査契約で分離する。

## 範囲

実フレーム校正前の診断強化のみ。帰還入力、stage遷移、global `RECALL_LIMIT`、retry backoffは変更しない。未知画面や別将軍のstatus panelから確定入力を送らない。

## 記録する証拠

- `fort_menu` / `fort_list` / `fort_status` の観測回数（各カウンターは `RECALL_LIMIT + 1` でbounded）。
- `fort_status_reads`: 選択対象将軍と一致したstatus panelの読取回数。
- 期待将軍と、最後にstatus panelから読めた将軍名を分離。
- 最後のscreen kindと、空白・改行を正規化して最大120文字に制限した画面文言。
- month audit projectionと生成Issue本文にも同じallowlisted fieldsを投影。OCR全文・raw frame・画面座標は追加せず、#5248の崖離脱先だけは2整数のgame-grid `target_cell` としてbounded projectionする。

## Hot-load marker

`hanjuku-chart-v1434-camp-dispatch-evidence`（当初 v1422、main再同期時のv1433から、#5248のdispatch分類修正を含めv1434へ再採番）。mainのv1432を基点に、v1433の砦証拠とv1434のdispatch/escape証拠をdiagnostics allowlist・固定値テストへ同期する。

## #5248 のdispatch分類

- `dispatch_unconfirmed` はA送信receipt未確認、`arrival_unconfirmed` は送信後の受理/到着未確認として、`resulting_event` を優先して分類する。
- `escape_move=true` は帰還要求ではなく崖離脱のフィールド移動。受理/未確認を `camp_escape_move_*` として記録し、通常の `recall_verification`、帰還先拒否回数、名簿到着確認を更新しない。
- 未確認の崖離脱移動は専用audit kindで追跡し、キャンプ帰還未確認の誤起票を避ける。

## 回帰テスト

- 3つの砦stageでglobal recall limit abort時のstage別観測数・screen kindを検証。
- fort_statusで期待将軍と観測将軍を分離し、不一致を読取成功に数えない。
- month audit projectionとIssue本文にbounded evidenceが出ることを検証。

## 未完了の受入

次回の砦帰還失敗で実フレーム由来のscreen evidenceを取得し、停滞箇所を特定する。その証拠なしに再遷移・待機動作を推測で変更しない。Issue #5252は実機再観測までOpenのまま維持する。
