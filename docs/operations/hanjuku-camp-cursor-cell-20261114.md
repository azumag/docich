# 半熟英雄: 野営発見の記録に統合カーソルセルを残す（v817、2026-11-14）

## 症状

野営発見（`camp_found`）のカメラ情報は、屋根から測った `origin`
（v808/v816）だけでした。屋根が0〜1個で `origin` が取れないとき、統合
カーソル（`mem['cursor']`）がどのセルを指しているのかを記録していなかった
ため、`camp_world` が設定されない局面で「統合カーソルを使えば測位できたの
か」を比較できませんでした。

## 修正（v817 `hanjuku-chart-v817-camp-cursor-cell`）

`camp_found` の `observed_metric` に `cursor_cell`（`mem['cursor']` の
ワールドセル）を追加します。挙動は不変更。

## 検証

- 新規 `test_a_camp_found_records_the_integrated_cursor_cell`:
  `cursor=[70,20]` のとき `cursor_cell=[70,20]` が記録される。
- 野営・帰還・月場面・field recall 関連の既存テストを実行。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- `camp_found` の `origin`（屋根）と `cursor_cell`（統合）を比較し、
  屋根が少ない局面でどちらが使えるかを判断できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
