# 半熟英雄: 帰還先ピッカーのフォールバックに近傍城一覧を残す（v824、2026-11-14）

## 症状（実測 g1163 2026-11-14 08:05）

帰還先ピッカーは goal が null のまま 90 observations の予算を使い切ると
`_picker_fallback` で「今いる城」を確定します。しかし記録には選んだ
`castle`（nearby の先頭）しか残らず、他に候補が何城あったのか（敵城か
自軍城か）を確認できませんでした。

なお、goal が null のときに即フォールバックする案は、既存の
`test_unknown_origin_does_not_borrow_old_integrated_cursor_or_picker_without_marker`
に反するため**取り下げ**ました。

## 修正（v824 `hanjuku-chart-v824-picker-fallback-nearby`）

`camp_recall_picker_fallback` の `observed_metric` に `nearby`（近傍城の
一覧全体）を追加します。挙動は不変更。

## 検証

- 新規 `test_the_picker_fallback_records_every_nearby_castle`:
  フォールバック記録に nearby が入る。
- 帰還先・野営・月場面・field recall 関連の既存テストを実行。
- 修正前の `map.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- フォールバック時に近傍へどの城（敵/自軍）がいたかを確認し、goal null の
  局面で安全な帰還先を選べるか判断できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
