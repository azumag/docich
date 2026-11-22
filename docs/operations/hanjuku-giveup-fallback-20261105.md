# 出撃元を諦める判定が効かないときは、パネルを閉じて観測を続ける（v418、2026-11-05）

## 症状（実測）

ボス指示 `A:bf8c3dee:M256898` で `order_source_changed` が **453件**、
すべて同じ `source: オケアナ`・`menu_miss` 3→4→5。

- この記録を出す `_give_up_source` は `mem['source_miss'][step]` を pop し
  `mem['source_override'][step]` を設定している。
- しかし永続化された policy を8秒おきに4回サンプルすると `source_miss` は **5のまま**、
  `source_override` に M256898 は **一度も現れない**（Issue #3417 と同じ兆候）。
- そのため「出撃元を諦める」判定が毎観測成立し続け、`_give_up_source` が**入力を返さずに return**。
  25分間 `order_start` が0件のまま停止。

## 変更

- `flows/map.py` に `_source_giveup_stuck(mem, step)` を追加（判定の共通化）。
- 諦め処理の後も同じ step が上限に達したままなら、**開いているパネルを閉じる `b` を1回返す**ようにした。
- 記録 `source_giveup_ineffective` に `source_miss` と `override` の実値を残し、次回は状態が
  残らなかったことをログで確認できる。

## 検証

- `tests/test_hanjuku_giveup_fallback.py`（新規2件）: 上限に達したままなら stuck と判定／
  きれいな状態は stuck でない。修正前は2件とも失敗、修正後 2 passed。
- 関連（`source_mismatch` / `chart_bot` / 層）338 passed。

## 未確認・残件

- state が残らない根本原因はランタイム側（Issue #3417）。本変更は「止まらない」ための対症処置。
- 実機で `order_start` が再開するかは未確認。
- 第6話ボス城への入城は依然として未実測。
